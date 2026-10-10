"""Causal, single-route research simulation; no provider or execution adapter."""

from datetime import timedelta
from decimal import Decimal, localcontext

from intraday.portfolio_coordinator import ParentPortfolioCoordinator, ParentPortfolioPolicy
from intraday.replay_v2.contracts import ReplayConfig, ReplayDataset
from intraday.replay_v2.ledger import Ledger, ONE
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
    threshold = max(book.day_start*(ONE-book.daily_loss), book.peak*(ONE-book.max_dd))
    guard_price = (threshold-book.cash)/book.quantity
    barrier = max(book.stop, guard_price)
    if candle.low <= barrier:
        price = min(candle.open, barrier)
        if guard_price >= book.stop:
            reason = "max_drawdown" if threshold == book.peak*(ONE-book.max_dd) else "daily_loss_limit"
            book.observe(at, price)
            book.halt(at, price, reason)
        else:
            reason = book.stop_reason
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


# Gate replays size one coin at the standard three-coin slot under the 60/40 split (ADR-006).
SETUP2_SLOT = Decimal(1)/3


def is_setup2(rule):
    return getattr(rule.parameters, "entry_profile", "donchian_v1") == "setup2_v1"


def _setup2(config, data, book, limitations, side=1):
    """Setup-2 on closed H4 bars: signal at close, fill at the next open, capped ATR trail."""
    from intraday import setup2
    from intraday.replay_v2.donchian_filter_book import ATRTrail
    series, observations = setup2.series_observations(list(data.candles), list(data.profile_candles),
                                                       side=side, anchor=data.indicator_anchor)
    if series is None:
        limitations.add("setup2_history_not_anchored")
        return
    book.stop_reason = "trailing_stop"
    trail, last = None, None
    for i, candle in enumerate(series):
        if not config.start <= candle.opened_at < config.end or candle.available_at > config.end:
            continue
        last = candle
        book.advance_day(candle.opened_at)
        book.observe(candle.opened_at, candle.open)
        closed = _risk_exit(book, candle.opened_at, candle.open, candle.open)
        if not closed and book.quantity and candle.open <= book.stop:
            book.close(candle.opened_at, candle.open, "trailing_stop_gap")
            closed = True
        signal = observations[i-1] if i else None
        if signal is None:
            limitations.add("setup2_warmup_inside_window")
        elif not closed:
            if book.quantity and signal.exit:
                book.close(candle.opened_at, candle.open, "donchian_exit")
                trail = None
            elif not book.quantity and signal.entry:
                if book.halted:
                    book.deny(candle.opened_at, candle.open, "portfolio_loss_limit")
                else:
                    stop_distance = min(3*signal.atr/candle.open, setup2.STOP_CAP)
                    target = min(config.capital, book.equity(candle.open))*book.entry_fraction*signal.size_multiplier
                    if book.enter(candle.opened_at, candle.open, target, side=side, stop_distance=stop_distance):
                        trail = ATRTrail(side, candle.open, signal.atr)
            elif not book.quantity and signal.donchian_entry:
                book.deny(candle.opened_at, candle.open, "setup2_filters",
                          failed_filters=list(signal.failed_filters), blockers=list(signal.blockers))
        _spot_intrabar(book, candle)
        at = candle.available_at-timedelta(milliseconds=1)
        _risk_exit(book, at, candle.close, candle.close)
        book.observe(at, candle.close)
        current = observations[i]
        if not book.quantity:
            trail = None
        elif trail is not None and current is not None and not current.exit and current.atr:
            # Closed bar ratchets the stop for the next bar; the 10% floor never loosens it.
            trail.update(candle.high, candle.low, current.atr)
            book.stop = max(book.stop, trail.stop) if side > 0 else min(book.stop, trail.stop)
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
    limitations.add("stored_binance_prices_not_verified_historical_demo_quotes")
    if data.rule.created_at > config.start:
        limitations.add("rule_selected_ex_post_not_out_of_sample")
    if config.profile.instrument is None:
        limitations.add("instrument_filters_not_verified")
    else:
        limitations.add("instrument_rules_are_a_timestamped_snapshot_not_historical_tiers")
    with localcontext() as context:
        context.prec = 28
        if config.market == "spot" and is_setup2(data.rule):
            book = Ledger(config, policy=ParentPortfolioPolicy.from_split(Decimal(".6"), Decimal(".4"), 1),
                          slot_weight=SETUP2_SLOT)
            limitations.update({"setup2_jev_filter_not_replayed", "spot_ohlc_path_assumed_adverse_first",
                                "spot_intrabar_timestamps_estimated", "setup2_m15_profile_uniform_volume",
                                "setup2_bar_level_fills"})
            _setup2(config, data, book, limitations)
            return build_result(config, data, book, limitations)
        book = Ledger(config)
        if config.market == "spot":
            limitations.update({"spot_jev_filter_not_replayed", "spot_ohlc_path_assumed_adverse_first",
                                "spot_intrabar_timestamps_estimated"})
            _spot(config, data, book, limitations)
        else:
            limitations.add("perp_risk_and_stops_sampled_at_recorded_quotes")
            replay_perp(config, data, book, limitations)
        return build_result(config, data, book, limitations)
