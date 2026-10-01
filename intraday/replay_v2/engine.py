"""Causal, single-route research simulation; no provider or execution adapter."""

from datetime import timedelta
from decimal import Decimal, localcontext

from intraday.portfolio_coordinator import ParentPortfolioCoordinator, ParentPortfolioPolicy
from intraday.replay_v2.contracts import ReplayConfig, ReplayDataset
from intraday.replay_v2.ledger import Ledger, DAILY_LOSS, MAX_DRAWDOWN, ONE
from intraday.replay_v2.metrics import build_result
from intraday.replay_v2.perp import replay_perp
from intraday.spot_signal import evaluate_donchian


def _risk_exit(book, at, mark, price):
    reason = book.guard_reason(mark)
    if reason:
        book.observe(at, mark)
        book.halt(at, mark, reason)
        book.close(at, price, reason)
        book.observe(at, price)
        return True
    return False


def _spot_intrabar(book, candle):
    """Assume open -> adverse -> favourable -> close; event times are bar-level."""
    if not book.quantity:
        return
    at = candle.available_at - timedelta(milliseconds=1)
    threshold = max(book.day_start*(ONE-DAILY_LOSS), book.peak*(ONE-MAX_DRAWDOWN))
    guard_price = (threshold-book.cash)/book.quantity
    barrier = max(book.stop, guard_price)
    if candle.low <= barrier:
        price = min(candle.open, barrier)
        if guard_price >= book.stop:
            reason = "max_drawdown" if threshold == book.peak*(ONE-MAX_DRAWDOWN) else "daily_loss_limit"
            book.observe(at, price)
            book.halt(at, price, reason)
        else:
            reason = "emergency_stop"
        book.close(at, price, reason)
        book.observe(at, price)
    else:
        book.observe(at, candle.low)
        book.observe(at, candle.high)


def _spot(config, data, book, limitations):
    rule = data.rule.parameters
    needed = max(rule.entry_window, rule.exit_window, rule.atr_period)+1
    coordinator = ParentPortfolioCoordinator(ParentPortfolioPolicy(initial_equity=float(config.capital)))
    rows = [c.row() for c in data.candles]
    last = None
    for index, candle in enumerate(data.candles):
        if not config.start <= candle.opened_at < config.end or candle.available_at > config.end:
            continue
        last = candle
        book.advance_day(candle.opened_at)
        book.observe(candle.opened_at, candle.open)
        closed = _risk_exit(book, candle.opened_at, candle.open, candle.open)
        if not closed and book.quantity and candle.open <= book.stop:
            book.close(candle.opened_at, candle.open, "emergency_stop_gap")
            closed = True
        window = data.candles[max(0, index-needed):index]
        contiguous = (len(window) == needed and window[-1].available_at == candle.opened_at and
                      all(a.available_at == b.opened_at for a,b in zip(window, window[1:])))
        observation = evaluate_donchian(rows[index-needed:index], rule) if contiguous else None
        if not contiguous:
            limitations.add("spot_warmup_or_history_gap")
        elif window[-1].available_at >= config.start and not closed:
            if book.quantity and observation.exit:
                book.close(candle.opened_at, candle.open, "donchian_exit")
            elif not book.quantity and observation.entry:
                size = Decimal(str(observation.size_multiplier))
                target = min(config.capital, book.equity(candle.open))*Decimal(".30")*size
                if book.halted:
                    book.deny(candle.opened_at, candle.open, "portfolio_loss_limit")
                elif target > 0:
                    authorization = coordinator.authorize_target(book.state(candle.opened_at, candle.open),
                        scope=config.scope, target_notional=float(target))
                    if authorization.allowed:
                        book.enter(candle.opened_at, candle.open, target, side=1, stop_distance=Decimal(".10"))
                    else:
                        book.deny(candle.opened_at, candle.open, ",".join(authorization.reason_codes))
        _spot_intrabar(book, candle)
        at = candle.available_at-timedelta(milliseconds=1)
        _risk_exit(book, at, candle.close, candle.close)
        book.observe(at, candle.close)
    if last:
        if last.available_at < config.end:
            limitations.add("price_history_ends_before_window")
        if book.quantity:
            book.close(last.available_at-timedelta(milliseconds=1), last.close, "window_end")
            book.observe(last.available_at-timedelta(milliseconds=1), last.close)
    else:
        limitations.add("no_closed_spot_bars_in_window")


def simulate(config: ReplayConfig, data: ReplayDataset) -> dict:
    if (data.rule.rule_id, data.rule.symbol, data.rule.scope) != (config.rule_id, config.symbol, config.scope):
        raise ValueError("replay rule identity mismatch")
    limitations = set(data.limitations)
    if config.profile.instrument is None:
        limitations.add("instrument_filters_not_verified")
    else:
        limitations.add("instrument_rules_are_a_timestamped_snapshot_not_historical_tiers")
    with localcontext() as context:
        context.prec = 28
        book = Ledger(config)
        if config.market == "spot":
            limitations.update({"spot_jev_filter_not_replayed", "spot_ohlc_path_assumed_adverse_first",
                                "spot_intrabar_timestamps_estimated"})
            _spot(config, data, book, limitations)
        else:
            limitations.add("perp_risk_and_stops_sampled_at_recorded_quotes")
            replay_perp(config, data, book, limitations)
        return build_result(config, data, book, limitations)
