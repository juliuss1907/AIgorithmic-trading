"""H1 signals, native H4/H8 trends, M15 Perp risk; parent/Spot remain H4."""

from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from intraday.contracts import SpotRuleParameters
from intraday.replay_v2.historical_mixed import (close_touched_stops, continuous_periods,
    funding_audit, historical_result, open_entries, open_exits, perp_observation,
    prepare_history, stop_fraction)
from intraday.replay_v2.intraday_book import IntradayBook
from intraday.replay_v2.intraday_data import validate_inputs as validate_native
from intraday.replay_v2.intraday_timeframes import consensus_at, trend_series
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.perp_daily import daily_summary
from intraday.replay_v2.portfolio_book import ZERO, ONE
from intraday.replay_v2.portfolio_research import validate_inputs as validate_spot
from intraday.spot_signal import evaluate_donchian


VERSION = 'historical-perp-intraday-timeframes-v1.0'
MS = timedelta(milliseconds=1)


class NativeClock:
    def __init__(self, series, start, end):
        self.opens, self.closes, self.rows, self.index = {}, {}, {}, {}
        for s, bars in series.items():
            self.rows[s] = [b.row() for b in bars]
            self.index[s] = {b.opened_at:i for i,b in enumerate(bars)}
            for bar in bars:
                if start <= bar.opened_at < end:
                    self.opens.setdefault(bar.opened_at, {})[s] = bar
                    self.closes.setdefault(bar.available_at-MS, {})[s] = bar

    def history(self, symbol, at):
        index = self.index[symbol][at]
        return self.rows[symbol][index-31:index]


def cost_checkpoint(book, at, marks):
    book.check_isolated_collateral(at)
    book.enforce_risk(at, marks)


def perp_open_exits(book, at, bars, observations, closed, trailing_open):
    for s, bar in sorted(bars.items()):
        p = book.perps[s]
        if not p.quantity:
            continue
        gap = bar.open <= p.stop if p.quantity > 0 else bar.open >= p.stop
        reason = (book.halt_reason if book.halted else book.daily.reason if book.daily.locked else
                  'contract_stop_gap' if gap else book.perp_exit_reason(s, bar.open, at) if trailing_open else None)
        obs = observations.get(s)
        if reason is None and obs is not None and (obs.exit_long if p.quantity > 0 else obs.exit_short):
            reason = 'donchian_exit'
        if reason:
            book.close_perp(s, at, bar.open, reason)
            closed.add(('perp', s))


def perp_open_entries(book, at, marks, bars, observations, trends, closed):
    cfg = book.config
    for s, obs in sorted(observations.items()):
        side = obs.entry_side
        if not side or book.perps[s].quantity or ('perp', s) in closed:
            continue
        if cfg.trend_filter and consensus_at(trends[s], at) != side:
            book.event(at, 'entry_blocked', 'h4_h8_trend_consensus', symbol=s, market='perp')
            continue
        distance = stop_fraction(cfg, bars[s].open, obs.atr)
        if not ZERO < distance < ONE:
            book.event(at, 'entry_blocked', 'invalid_stop_distance', symbol=s, market='perp')
            continue
        if book.halted:
            book.event(at, 'entry_blocked', 'portfolio_loss_limit', symbol=s, market='perp')
            continue
        target = book.perp_budget(marks)*cfg.perp_weights[s]*2/3 if cfg.perp_size == 'two-thirds' else None
        if book.enter_perp(s, at, marks, bars[s].open, side, distance, approved_target=target):
            book.events[-1].update(reason='historical_donchian', signal_interval='1h',
                trend_profile=cfg.perp_trend_profile, stop_fraction=str(distance),
                stop_price=str(book.perps[s].stop), signal_available_at=at.isoformat())
            cost_checkpoint(book, at, marks)


def simulate_intraday(config, candles, daily, perp, funding, native):
    validate_native(config, native, perp)
    _, spot, spot_trends = validate_spot(config, candles, daily)
    prepare_history(config, perp)  # The frozen base remains independently valid.
    spot_clock = NativeClock(spot, config.start, config.end)
    signal = NativeClock({s:p['trade1h'] for s,p in native.items()}, config.start, config.end)
    contract = NativeClock({s:p['trade15m'] for s,p in native.items()}, config.start, config.end)
    mark_clock = NativeClock({s:p['mark15m'] for s,p in native.items()}, config.start, config.end)
    trail = signal if config.perp_trailing_interval == '1h' else contract
    trends = {s:(trend_series(p['trade4h']), trend_series(p['trade8h'])) for s,p in native.items()}
    audit, settlements = funding_audit(config, funding), defaultdict(list)
    book = IntradayBook(config)
    for s, state in audit.items():
        if not state['complete']:
            book.limitations.add('funding_coverage_incomplete:'+s)
        for row in funding[s].settlements if s in funding else ():
            if config.start <= row.at < config.end:
                settlements[row.at].append((s, row))
    marks = {s:b.open for s,b in spot_clock.opens[config.start].items()}
    book.perp_marks = {s:b.open for s,b in mark_clock.opens[config.start].items()}
    recently_closed = {}
    times = sorted(set(contract.opens) | set(contract.closes) | set(settlements) | {config.end})
    for at in times:
        book.advance_day(at)
        closed = recently_closed.pop(at, set())
        spot_close, perp_close = spot_clock.closes.get(at, {}), contract.closes.get(at, {})
        spot_open, perp_open = spot_clock.opens.get(at, {}), contract.opens.get(at, {})
        marks.update({s:b.close for s,b in spot_close.items()})
        marks.update({s:b.open for s,b in spot_open.items()})
        book.perp_marks.update({s:b.close for s,b in mark_clock.closes.get(at, {}).items()})
        book.perp_marks.update({s:b.open for s,b in mark_clock.opens.get(at, {}).items()})
        for s, row in sorted(settlements.get(at, ())):
            book.perp_marks[s] = row.mark
            book.settle_funding(s, at, row.rate, row.mark)
        before = len(book.trades)
        if perp_close or spot_close:
            closed |= close_touched_stops(book, at, spot_close, perp_close)
            recently_closed[at+MS] = closed
        parent_tick = bool(spot_open or spot_close or at in settlements or at == config.end)
        if parent_tick or len(book.trades) != before:
            cost_checkpoint(book, at, marks)
        else:
            book.check_isolated_collateral(at)
            book.enforce_perp_risk(at)
        if not book.halted and not book.daily.locked and at in trail.closes:
            book.observe_trade_trailing(at, trail.closes[at])
        if at == config.end:
            last = at-MS
            for s, bar in sorted(spot_clock.closes[last].items()):
                book.close(s, at, bar.close, 'window_end')
            for s, bar in sorted(contract.closes[last].items()):
                book.close_perp(s, at, bar.close, 'window_end')
            cost_checkpoint(book, at, marks)
            continue
        if not perp_open:
            continue
        spot_obs = {s:evaluate_donchian(spot_clock.history(s, at),
            SpotRuleParameters(entry_window=30, exit_window=8, atr_period=14)) for s in spot_open}
        perp_obs = {s:perp_observation(signal.history(s, at)) for s in signal.opens.get(at, {})}
        before = len(book.trades)
        open_exits(book, at, spot_open, {}, spot_obs, {}, closed)
        perp_open_exits(book, at, perp_open, perp_obs, closed, at in trail.opens)
        if len(book.trades) != before:
            cost_checkpoint(book, at, marks)
        # Parent resumption is deliberately not a new periodic M15 parent guard.
        if spot_open:
            book.maybe_resume(at, marks)
        book.maybe_resume_perp(at)
        if spot_open:
            open_entries(book, at, marks, {}, spot_obs, {}, spot_trends, {}, closed)
        if perp_obs:
            perp_open_entries(book, at, marks, perp_open, perp_obs, trends, closed)
    return intraday_result(config, spot, daily, perp, funding, audit, book, native)


def intraday_result(config, spot, daily, perp, funding, audit, book, native):
    report = historical_result(config, spot, daily, perp, funding, audit, book)
    base_hash = report['inputs']['dataset_checksum']
    native_hash = fingerprint({s:{tag:[b.row() for b in bars] for tag,bars in p.items()}
                               for s,p in sorted(native.items())})
    data_hash = fingerprint({'base': base_hash, 'intraday': native_hash})
    report.update(evaluator_version=VERSION,
        result_id=fingerprint({'version': VERSION, 'config': report['config'], 'data': data_hash}),
        perp_equity_curve=book.perp_curve)
    report['inputs'].update(dataset_checksum=data_hash, base_dataset_checksum=base_hash,
                            intraday_dataset_checksum=native_hash)
    valid = report['status'] == 'complete'
    report['summary']['perp_daily'] = daily_summary(book, valid, curve=book.perp_curve)
    report['summary']['perp_daily']['six_month_periods'] = continuous_periods(
        config.model_copy(update={'capital': book.daily.initial}),
        [{**r, 'equity_known':r['perp_equity']} for r in book.perp_curve])
    report['methodology'].update(
        signals='Perp: closed native H1 Donchian 30/8; closed H4 AND H8 EMA50 direction and slope consensus; Spot unchanged H4/D1; no Jev/confidence',
        perp_stop='ATR14 simple mean TR on closed H1 x3 fixed at entry; gap M15 open, touch detected M15 close',
        risk_sampling='Perp M15 mark open/close and funding/cost events; parent H4 plus funding/cost events only; Spot marks H4 as-of',
        fills='native contract next open: H1 signals, M15 daily/parent Perp flatten; trailing next open of its own interval; Spot H4 unchanged')
    report['methodology']['perp_trade_trailing']['sampling_interval'] = config.perp_trailing_interval
    report['methodology']['perp_daily']['fills'] = 'M15 close/funding trigger -> next M15 contract open; open trigger -> that open'
    report['methodology']['perp_daily']['priority'] = 'parent -> Perp daily -> ATR gap -> trailing -> H1 Donchian -> entries'
    report['limitations'] = sorted((set(report['limitations'])-{'four_hour_sampling_not_intraday_jev_llm_execution'}) | {
        'quantitative_intraday_research_not_current_jev_llm_execution',
        'spot_rule_unchanged_but_shared_cash_and_parent_halts_can_change_spot_fills',
        'm15_ohlc_stops_detected_at_close_unknown_intrabar_order',
        'parent_risk_remains_h4_not_m15'})
    return report
