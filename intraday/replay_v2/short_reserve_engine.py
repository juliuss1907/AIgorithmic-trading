"""H4/D1 short-only signals and native M15 Perp risk; no trade trailing."""

from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal

from intraday.contracts import SpotRuleParameters
from intraday.spot_signal import evaluate_donchian
from intraday.replay_v2.historical_mixed import (close_touched_stops, continuous_periods,
    funding_audit, historical_result, open_entries, open_exits, perp_observation, prepare_history)
from intraday.replay_v2.intraday_data import validate_inputs as validate_native
from intraday.replay_v2.intraday_engine import (MS, NativeClock, cost_checkpoint,
    flatten_open_locks, perp_open_exits)
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.perp_daily import daily_summary
from intraday.replay_v2.portfolio_book import ZERO
from intraday.replay_v2.portfolio_research import validate_inputs as validate_spot
from intraday.replay_v2.short_reserve_book import ShortReserveBook


VERSION = 'historical-perp-short-reserve-v1.0'
PARAMETERS = SpotRuleParameters(entry_window=30, exit_window=8, atr_period=14)


def short_entries(book, at, marks, clock, bars, observations, trends, closed, spot_open):
    for s, obs in sorted(observations.items()):
        if obs.entry_side != -1 or book.perps[s].quantity or ('perp', s) in closed:
            continue
        times, sides = trends[s]
        index = bisect_right(times, at)-1
        if book.config.trend_filter and (index < 0 or sides[index] != -1):
            book.event(at, 'entry_blocked', 'daily_trend_filter', symbol=s, market='perp')
            continue
        multiplier = Decimal(str(evaluate_donchian(clock.history(s, at), PARAMETERS).size_multiplier))
        target = book.perp_budget(marks)*book.config.perp_weights[s]*book.config.leverage*multiplier
        if book.enter_perp(s, at, marks, bars[s].open, -1, Decimal('.10'), approved_target=target):
            book.events[-1].update(reason='historical_donchian', signal_interval='4h',
                stop_fraction='.10', stop_price=str(book.perps[s].stop),
                margin_budget=str(book.perp_budget(marks)), atr_size_multiplier=str(multiplier),
                signal_available_at=at.isoformat())
            cost_checkpoint(book, at, marks)
            flatten_open_locks(book, at, marks, spot_open, bars, closed)


def simulate_short_reserve(config, candles, daily, perp, funding, native):
    validate_native(config, native, perp)
    _, spot, spot_trends = validate_spot(config, candles, daily)
    signals, _, trends = prepare_history(config, perp)
    spot_clock = NativeClock(spot, config.start, config.end)
    signal = NativeClock(signals, config.start, config.end)
    contract = NativeClock({s:p['trade15m'] for s,p in native.items()}, config.start, config.end)
    mark_clock = NativeClock({s:p['mark15m'] for s,p in native.items()}, config.start, config.end)
    audit, settlements = funding_audit(config, funding), defaultdict(list)
    book = ShortReserveBook(config)
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
        if spot_open or spot_close or at in settlements or at == config.end or len(book.trades) != before:
            cost_checkpoint(book, at, marks)
        else:
            book.check_isolated_collateral(at)
            book.enforce_perp_risk(at)
        if book.flat_at == at:
            book.rebalance_reserve(at, marks)
        if at == config.end:
            for s, bar in sorted(spot_clock.closes[at-MS].items()):
                book.close(s, at, bar.close, 'window_end')
            for s, bar in sorted(contract.closes[at-MS].items()):
                book.close_perp(s, at, bar.close, 'window_end')
            cost_checkpoint(book, at, marks)
            continue
        if not perp_open:
            continue
        spot_obs = {s:evaluate_donchian(spot_clock.history(s, at), PARAMETERS) for s in spot_open}
        perp_obs = {s:perp_observation(signal.history(s, at)) for s in signal.opens.get(at, {})}
        before = len(book.trades)
        open_exits(book, at, spot_open, {}, spot_obs, {}, closed)
        perp_open_exits(book, at, perp_open, perp_obs, closed, False)
        if len(book.trades) != before:
            cost_checkpoint(book, at, marks)
        flatten_open_locks(book, at, marks, spot_open, perp_open, closed)
        if book.flat_at == at:
            book.rebalance_reserve(at, marks)
        if spot_open:
            book.maybe_resume(at, marks)
        book.maybe_resume_perp(at)
        if spot_open:
            open_entries(book, at, marks, {}, spot_obs, {}, spot_trends, {}, closed)
            flatten_open_locks(book, at, marks, spot_open, perp_open, closed)
        if perp_obs:
            short_entries(book, at, marks, signal, perp_open, perp_obs, trends, closed, spot_open)
        if book.flat_at == at:
            book.rebalance_reserve(at, marks)
    return short_result(config, spot, daily, perp, funding, audit, book, native)


def short_result(config, spot, daily, perp, funding, audit, book, native):
    report = historical_result(config, spot, daily, perp, funding, audit, book)
    base_hash = report['inputs']['dataset_checksum']
    native_hash = fingerprint({s:{tag:[b.row() for b in bars] for tag,bars in p.items()}
                               for s,p in sorted(native.items())})
    data_hash = fingerprint({'base':base_hash, 'intraday':native_hash})
    report.update(evaluator_version=VERSION,
        result_id=fingerprint({'version':VERSION, 'config':report['config'], 'data':data_hash}),
        perp_equity_curve=book.perp_curve, reserve_transfers=book.transfers)
    report['inputs'].update(dataset_checksum=data_hash, base_dataset_checksum=base_hash,
                            intraday_dataset_checksum=native_hash)
    net = sum((t['net_pnl'] for t in book.trades if t['market'] == 'perp'), ZERO)
    p, r = book.realized_capital['perp'], book.reserve_balance
    if (abs(p-book.daily.initial-net-book.drawn+book.repaid) > Decimal('1e-18') or
            r != book.reserve_initial-book.drawn+book.repaid or not ZERO <= r <= book.reserve_initial):
        raise ValueError('reserve transfers and Perp trading PnL do not reconcile')
    report['summary']['reserve'] = dict(policy=config.reserve_policy, initial=float(book.reserve_initial),
        final=float(r), drawn=float(book.drawn), repaid=float(book.repaid),
        net_debt=float(book.reserve_initial-r), transfer_count=len(book.transfers),
        actual_perp_capital=float(p), performance_equity=float(p-book.daily.capital_flows), reconciled=True)
    d = daily_summary(book, report['status'] == 'complete', curve=book.perp_curve)
    d['six_month_periods'] = continuous_periods(config.model_copy(update={'capital':book.daily.initial}),
        [{**row, 'equity_known':row['perp_performance_equity']} for row in book.perp_curve])
    report['summary']['perp_daily'] = d
    report['methodology'].update(
        signals='Spot H4/D1 unchanged; Perp short-only closed H4 Donchian 30/8, D1 EMA50 declining with close below EMA; no Jev/LLM',
        allocation='Spot own realized budget600; Perp margin budget300 isolated2x, ATR14 sizing both; actual reserve cash floor100 minus draws plus repayments; no combined notional cap; no unrealized sizing',
        perp_stop='fixed emergency entry price +10%; native M15 open gap or high touch detected at close; no ATR stop or trailing',
        risk_sampling='Perp M15 mark open/close and funding/cost; parent H4 and funding/cost only',
        daily_reset='next UTC day AND Perp flat AND parent safe; reserve flows never reset a lock or peak',
        reserve='all Perps flat after exit batch/risk only; restore300 then repay100; transfers conserve cash, no window_end replenishment',
        fills='H4 signal next H4 open; M15 daily close/funding breach next M15 open, open breach same open; no entry on flat-batch timestamp')
    report['methodology']['perp_daily'].update(capital_basis='own marked sleeve, actual funded day-start; net capital flows excluded from daily return and cumulative trading DD',
        priority='parent -> Perp daily -> emergency gap -> H4 Donchian -> entries',
        fills='M15 close/funding trigger next M15 open; open/cost trigger same open')
    report['limitations'] = sorted((set(report['limitations'])-{'four_hour_sampling_not_intraday_jev_llm_execution'}) | {
        'm15_ohlc_stops_detected_at_close_unknown_intrabar_order', 'parent_risk_remains_h4_not_m15',
        'reserve_not_live_margin_topup_or_liquidation_insurance',
        'spot_rule_unchanged_but_shared_cash_and_parent_halts_can_change_spot_fills'})
    return report
