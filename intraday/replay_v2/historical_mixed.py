"""Historical quantitative Perp research; never fabricates Jev decisions."""

from dataclasses import dataclass
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, field_validator, model_validator

from intraday.contracts import SpotRuleParameters
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.mixed_book import MixedBook, MixedConfig
from intraday.replay_v2.perp_daily import PerpDailyBook, daily_summary
from intraday.replay_v2.portfolio_book import ONE, ZERO, STOP
from intraday.replay_v2.portfolio_research import WIDTH, daily_points, validate_inputs
from intraday.replay_v2.study import months_before
from intraday.spot_signal import evaluate_donchian


class HistoricalConfig(MixedConfig):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False, validate_default=True)
    exit_window: Literal[8] = 8
    perp_stop: Literal['fixed-1pct', 'atr14-2x', 'fixed-5pct', 'atr14-3x'] = 'fixed-1pct'
    perp_size: Literal['full', 'two-thirds'] = 'full'
    perp_daily_policy: Literal['disabled', 'none', 'target3', 'target5', 'trailing'] = 'disabled'

    @model_validator(mode='after')
    def daily_policy_requires_perp(self):
        if self.perp_daily_policy != 'disabled' and not self.include_perp:
            raise ValueError('daily policy requires Perp; use disabled for Spot-only control')
        return self

    @field_validator('weights', 'perp_weights')
    @classmethod
    def canonical_order(cls, value):
        # Decimal summation order must survive canonical JSON serialization.
        return dict(sorted(value.items()))


VERSION = 'historical-mixed-quant-v1.2'  # Explicit stop and notional-size experiments.
DAILY_VERSION = 'historical-perp-daily-policy-v1.0'


@dataclass(frozen=True)
class PerpObservation:
    entry_side: int
    exit_long: bool
    exit_short: bool
    atr: Decimal


def perp_observation(rows):
    obs = evaluate_donchian(rows, SpotRuleParameters(entry_window=30, exit_window=8, atr_period=14))
    close = Decimal(str(rows[-1][4]))
    low_entry = min(Decimal(str(row[3])) for row in rows[-31:-1])
    high_exit = max(Decimal(str(row[2])) for row in rows[-9:-1])
    return PerpObservation(1 if obs.entry else -1 if close < low_entry else 0,
        obs.exit, close > high_exit, Decimal(str(obs.atr)))


def daily_directions(rows):
    validated = daily_points(rows)
    closes, ema, prior, sides = [], None, None, []
    for row in rows:
        close = Decimal(str(row[4]))
        closes.append(close)
        if len(closes) == 50:
            ema = sum(closes)/50
        elif ema is not None:
            prior = ema
            ema += Decimal(2)/51*(close-ema)
        sides.append(1 if prior is not None and close > ema > prior else
                     -1 if prior is not None and close < ema < prior else 0)
    return [point[0] for point in validated], sides


def stop_fraction(config, entry_price, atr):
    fixed = {'fixed-1pct': Decimal('.01'), 'fixed-5pct': Decimal('.05')}
    if config.perp_stop in fixed:
        return fixed[config.perp_stop]
    return {'atr14-2x': 2, 'atr14-3x': 3}[config.perp_stop]*atr/entry_price


def prepare_history(config, perp):
    if set(perp) != set(config.perp_weights):
        raise ValueError('historical Perp inputs require every configured coin')
    projection = config.model_copy(update={'weights': config.perp_weights})
    timeline, prepared, _ = validate_inputs(projection, {s: p['trade'] for s, p in perp.items()},
                                         {s: p['daily'] for s, p in perp.items()})
    trends, marks = {}, {}
    for s, data in perp.items():
        rows = tuple(data['mark'])
        if tuple(c.opened_at for c in rows) != timeline or any(
            c.available_at != c.opened_at+WIDTH for c in rows):
            raise ValueError(s+': incomplete native mark-price window')
        marks[s] = {c.opened_at: c for c in rows}
        trends[s] = daily_directions(data['daily']) if config.trend_filter else ([], [])
    return prepared, marks, trends


def funding_audit(config, funding):
    """Audit returned history, without inventing settlements or filling gaps."""
    result = {}
    for s in config.perp_weights:
        h = funding.get(s)
        if h is not None and h.symbol != s:
            raise ValueError('funding belongs to another coin')
        times = [p.at for p in h.settlements if config.start <= p.at < config.end] if h else []
        gaps = [(b-a).total_seconds() for a, b in zip(times, times[1:])]
        # BTC/ETH conservative completeness guard, not an assumed funding schedule.
        tolerance = timedelta(seconds=1)  # API records observed millisecond settlement jitter.
        complete = bool(h and h.coverage_start <= config.start and h.coverage_end >= config.end and times
            and times[0]-config.start <= timedelta(hours=8)+tolerance
            and config.end-times[-1] <= timedelta(hours=8)+tolerance
            and max(gaps, default=0) <= 8*3600+tolerance.total_seconds())
        result[s] = {'complete': complete, 'settlements': len(times),
            'first': times[0].isoformat() if times else None, 'last': times[-1].isoformat() if times else None,
            'maximum_interval_seconds': max(gaps, default=0), 'gap_guard_seconds': 8*3600,
            'timestamp_tolerance_seconds': tolerance.total_seconds()}
    return result


def close_touched_stops(book, at, spot_bars, perp_bars):
    closed = set()
    for s, bar in sorted(spot_bars.items()):
        p = book.positions[s]
        if p.quantity and bar.low <= p.entry_price*(ONE-STOP):
            book.close(s, at, p.entry_price*(ONE-STOP), 'emergency_stop_detected_at_close')
            closed.add(('spot', s))
    for s, bar in sorted(perp_bars.items()):
        p = book.perps[s]
        if p.quantity and (bar.low <= p.stop if p.quantity > 0 else bar.high >= p.stop):
            book.close_perp(s, at, p.stop, 'contract_stop_detected_at_close')
            closed.add(('perp', s))
    return closed


def open_exits(book, at, spot_bars, perp_bars, spot_obs, perp_obs, closed):
    for s, bar in sorted(spot_bars.items()):
        p = book.positions[s]
        reason = (book.halt_reason if book.halted else 'emergency_stop_gap'
            if p.quantity and bar.open <= p.entry_price*(ONE-STOP) else
            'donchian_exit' if spot_obs[s].exit else None)
        if p.quantity and reason:
            book.close(s, at, bar.open, reason)
            closed.add(('spot', s))
    for s, bar in sorted(perp_bars.items()):
        p, obs = book.perps[s], perp_obs[s]
        gap = p.quantity and (bar.open <= p.stop if p.quantity > 0 else bar.open >= p.stop)
        daily_reason = book.daily.reason if isinstance(book, PerpDailyBook) and book.daily.locked else None
        reason = (book.halt_reason if book.halted else daily_reason if daily_reason else 'contract_stop_gap' if gap else
                  'donchian_exit' if (obs.exit_long if p.quantity > 0 else obs.exit_short) else None)
        if p.quantity and reason:
            book.close_perp(s, at, bar.open, reason)
            closed.add(('perp', s))


def open_entries(book, at, marks, perp_bars, spot_obs, perp_obs, spot_trends, perp_trends, closed):
    cfg = book.config
    requests = {}
    for s, obs in sorted(spot_obs.items()):
        if not obs.entry or book.positions[s].quantity or ('spot', s) in closed:
            continue
        allowed = not cfg.trend_filter or spot_trends[s][1][bisect_right(spot_trends[s][0], at)-1][1]
        if allowed:
            requests[s] = Decimal(str(obs.size_multiplier))
        else:
            book.event(at, 'entry_blocked', 'daily_trend_filter', symbol=s, market='spot')
    book.enter_batch(at, marks, requests)
    book.enforce_risk(at, marks)
    if not cfg.include_perp:
        return
    for s, obs in sorted(perp_obs.items()):
        side = obs.entry_side
        if not side or book.perps[s].quantity or ('perp', s) in closed:
            continue
        if cfg.trend_filter and perp_trends[s][1][bisect_right(perp_trends[s][0], at)-1] != side:
            book.event(at, 'entry_blocked', 'daily_trend_filter', symbol=s, market='perp')
            continue
        distance = stop_fraction(cfg, perp_bars[s].open, obs.atr)
        if not ZERO < distance < ONE:
            book.event(at, 'entry_blocked', 'invalid_stop_distance', symbol=s, market='perp')
            continue
        if book.halted:
            book.event(at, 'entry_blocked', 'portfolio_loss_limit', symbol=s, market='perp')
        else:
            # Scale the requested coin budget BEFORE cash/exposure trimming, not
            # the already-constrained target. Unused allocation stays in cash.
            target = (min(cfg.capital, book.equity(marks))*cfg.perp_cap*cfg.perp_weights[s]*2/3
                      if cfg.perp_size == 'two-thirds' else None)
            if book.enter_perp(s, at, marks, perp_bars[s].open, side, distance, approved_target=target):
                book.events[-1].update(reason='historical_donchian', stop_fraction=str(distance),
                                      stop_price=str(book.perps[s].stop), signal_available_at=at.isoformat())
        book.enforce_risk(at, marks)


def simulate_historical(config, candles, daily, perp, funding):
    opens, spot, spot_trends = validate_inputs(config, candles, daily)
    trades, mark_bars, perp_trends = prepare_history(config, perp)
    spot_idx = {s: {c.opened_at: i for i, c in enumerate(rows)} for s, rows in spot.items()}
    perp_idx = {s: {c.opened_at: i for i, c in enumerate(rows)} for s, rows in trades.items()}
    spot_rows = {s: [c.row() for c in rows] for s, rows in spot.items()}
    perp_rows = {s: [c.row() for c in rows] for s, rows in trades.items()}
    settlements = defaultdict(list)
    audit = funding_audit(config, funding)
    book = PerpDailyBook(config) if config.include_perp and config.perp_daily_policy != 'disabled' else MixedBook(config)
    if config.include_perp:
        for s, state in audit.items():
            if not state['complete']:
                book.limitations.add('funding_coverage_incomplete:'+s)
            for row in funding[s].settlements if s in funding else ():
                if config.start <= row.at < config.end:
                    settlements[row.at].append((s, row))
    marks = {s: spot[s][spot_idx[s][config.start]].open for s in spot}
    book.perp_marks = {s: mark_bars[s][config.start].open for s in trades}
    # Native Binance closeTime is the last millisecond of the candle. Keeping
    # close and next open separate also attributes the 20:00 bar to its UTC day.
    closing_times = {at+WIDTH-timedelta(milliseconds=1): at for at in opens}
    opening_times = set(opens)
    recently_closed = {}
    for at in sorted(opening_times | set(closing_times) | {config.end} | set(settlements)):
        book.advance_day(at)
        closed = recently_closed.pop(at, set())
        if at in closing_times:
            previous = closing_times[at]
            spot_close = {s: spot[s][spot_idx[s][previous]] for s in spot}
            perp_close = {s: trades[s][perp_idx[s][previous]] for s in trades}
            marks.update({s: c.close for s, c in spot_close.items()})
            book.perp_marks.update({s: mark_bars[s][previous].close for s in trades})
        if at in opening_times:
            spot_open = {s: spot[s][spot_idx[s][at]] for s in spot}
            perp_open = {s: trades[s][perp_idx[s][at]] for s in trades}
            marks.update({s: c.open for s, c in spot_open.items()})
            book.perp_marks.update({s: mark_bars[s][at].open for s in trades})
        for s, row in sorted(settlements[at]):
            book.perp_marks[s] = row.mark
            book.settle_funding(s, at, row.rate, row.mark)
        if at in closing_times:
            closed = close_touched_stops(book, at, spot_close, perp_close)
            recently_closed[at+timedelta(milliseconds=1)] = closed
        book.check_isolated_collateral(at)
        book.enforce_risk(at, marks)
        if at == config.end:
            for s in sorted(spot):
                book.close(s, at, spot_close[s].close, 'window_end')
            for s in sorted(trades):
                book.close_perp(s, at, perp_close[s].close, 'window_end')
            book.enforce_risk(at, marks)
        elif at in opening_times:
            spot_obs = {s: evaluate_donchian(spot_rows[s][spot_idx[s][at]-31:spot_idx[s][at]],
                SpotRuleParameters(entry_window=30, exit_window=8, atr_period=14)) for s in spot}
            perp_obs = {s: perp_observation(perp_rows[s][perp_idx[s][at]-31:perp_idx[s][at]]) for s in trades}
            open_exits(book, at, spot_open, perp_open, spot_obs, perp_obs, closed)
            book.enforce_risk(at, marks)
            book.maybe_resume(at, marks)
            if isinstance(book, PerpDailyBook):
                book.maybe_resume_perp(at)
            open_entries(book, at, marks, perp_open, spot_obs, perp_obs, spot_trends, perp_trends, closed)
    return historical_result(config, spot, daily, perp, funding, audit, book)


def continuous_periods(config, curve):
    boundaries = sorted({config.start, config.end} | {
        months_before(config.end, n) for n in (6, 12, 18)
        if config.start < months_before(config.end, n) < config.end})
    initial, results = config.capital, []
    for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        values = [Decimal(p['equity_known']) for p in curve if
            (start <= datetime.fromisoformat(p['at']) if i == 0 else start < datetime.fromisoformat(p['at']))
            and datetime.fromisoformat(p['at']) <= end]
        peak, dd = initial, ZERO
        for value in values:
            peak = max(peak, value)
            dd = max(dd, ONE-value/peak)
        final = values[-1] if values else initial
        results.append(dict(start=start.isoformat(), end=end.isoformat(),
            return_pct=float((final/initial-ONE)*100), pnl=float(final-initial), max_drawdown_pct=float(dd*100)))
        initial = final
    return results


def historical_result(config, spot, daily, perp, funding, audit, book):
    if not book.flat:
        raise ValueError('historical mixed research must end flat')
    net = book.cash-config.capital
    if abs(sum((t['net_pnl'] for t in book.trades), ZERO)-net) > Decimal('1e-18'):
        raise ValueError('historical trade PnL does not reconcile to shared cash')
    contributions = {}
    for market, symbols in (('spot', config.weights), ('perp', config.perp_weights)):
        for s in sorted(symbols):
            rows = [t for t in book.trades if t['market'] == market and t['symbol'] == s]
            contributions[market+':'+s] = {'closed_trades': len(rows), **{
                name: float(sum((t[name] for t in rows), ZERO)) for name in
                ('gross_pnl', 'exchange_fee', 'slippage_cost', 'funding_paid', 'net_pnl')}}
    complete = not config.include_perp or all(p['complete'] for p in audit.values())
    valid = complete and not {'unsupported_liquidation', 'perp_capital_exhausted'} & book.limitations
    blockers = ([] if net > 0 else ['nonpositive_net_return']) + (
        ['drawdown_at_or_above_limit'] if book.max_drawdown >= config.max_drawdown else []) + (
        ['minimum_6_closed_trades'] if len(book.trades) < 6 else [])
    payload = {**config.model_dump(mode='json'), 'signal_mode': 'historical_deterministic',
        'symbol': '+'.join(s.removesuffix('USDT') for s in sorted(config.weights)),
        'market': 'spot+perp' if config.include_perp else 'spot-control',
        'entry_window': 30, 'atr_period': 14, 'perp_exit_window': 8}
    if config.perp_daily_policy == 'disabled':
        payload.pop('perp_daily_policy')  # Preserve old config hashes and journal identities.
    version = DAILY_VERSION if isinstance(book, PerpDailyBook) else VERSION
    data_hash = fingerprint({'spot': {s: [c.row() for c in rows] for s, rows in sorted(spot.items())},
        'spot_daily': daily, 'perp': {s: {'trade': [c.row() for c in p['trade']],
            'mark': [c.row() for c in p['mark']], 'daily': p['daily']} for s, p in sorted(perp.items())},
        'funding': {s: h.model_dump(mode='json') for s, h in sorted(funding.items())}})
    terminal = next((e['at'] for e in book.events if e['kind'] == 'halt' and e['reason'] == 'max_drawdown'), None)
    report = {'schema_version': '2', 'evaluator_version': version,
        'result_id': fingerprint({'version': version, 'config': payload, 'data': data_hash}),
        'research_only': True, 'activation_allowed': False, 'official_gate_eligible': False,
        'status': 'complete' if valid else 'limited', 'config': payload,
        'inputs': {'dataset_checksum': data_hash, 'config_checksum': fingerprint(payload), 'funding_audit': audit},
        'summary': {'initial_capital': float(config.capital), 'final_equity_known': float(book.cash),
            'net_pnl': float(net) if valid else None, 'pnl_after_known_costs': float(net),
            'net_return_pct': float(net/config.capital*100) if valid else None,
            'funding_complete': complete, 'funding_paid_known': float(book.funding_paid),
            'exchange_fee_known': float(book.fees), 'slippage_cost_known': float(book.slippage),
            'max_drawdown_known_pct': float(book.max_drawdown*100), 'closed_trades': len(book.trades),
            'max_exposure_pct': float(book.max_exposure*100), 'max_perp_notional_pct': float(book.max_perp_exposure*100),
            'max_isolated_margin_pct': float(book.max_margin*100), 'contributions': contributions,
            'perp_sides': {side: {'closed_trades': sum(t['market'] == 'perp' and t['side'] == side for t in book.trades),
                'net_pnl': float(sum((t['net_pnl'] for t in book.trades if t['market'] == 'perp' and t['side'] == side), ZERO))}
                for side in ('long', 'short')},
            'perp_stop_exit_count': sum(t['market'] == 'perp' and t['exit_reason'].startswith('contract_stop')
                                        for t in book.trades),
            'blocked_entries': dict(Counter(e['reason'] for e in book.events if e['kind'] == 'entry_blocked')),
            'daily_pause_count': sum(e['kind'] == 'halt' and e['reason'] == 'daily_loss_limit' for e in book.events),
            'daily_resume_count': sum(e['kind'] == 'resume' for e in book.events),
            'halted': book.halted, 'halt_reason': book.halt_reason, 'terminal_halt_at': terminal,
            'active_days_until_terminal': ((datetime.fromisoformat(terminal) if terminal else config.end)-config.start).total_seconds()/86400,
            'six_month_periods': continuous_periods(config, book.curve),
            'economic_check_only': ('pass' if not blockers else 'reject') if valid else 'not_evaluated_incomplete',
            'blockers': blockers},
        'methodology': {'official_gate_eligible': False, 'signal_mode': 'historical_deterministic',
            'signals': 'closed native 4h Donchian 30/8, native 1d EMA50 and slope; no Jev or confidence',
            'fills': 'contract-price next open; stops gap at open or touch at stop detected at close',
            'native_close_timestamp': 'Binance closeTime, end-exclusive minus 1ms; signals use availability at next boundary',
            'funding': 'actual settlements before same-time exits/entries; touched positions exist until detection',
            'risk_sampling': 'native 4h open/close and funding/cost events; between-sample DD unknown',
            'daily_reset': 'next UTC day AND flat, equity peak never reset; DD terminal',
            'risk_limits': {'daily_loss_pct': float(config.daily_loss*100), 'max_drawdown_pct': float(config.max_drawdown*100)},
            'costs': {'spot_fee_bps': 10, 'spot_slippage_bps': 5, 'perp_fee_bps': 5, 'perp_slippage_bps': 5,
                'spot_fee_source': 'https://www.binance.com/en/fee/trading',
                'perp_fee_source': 'https://www.binance.com/en/fee/futureFee', 'fee_observed_at': '2026-10-01T00:00:00Z',
                'historical_account_fee_verified': False},
            'perp_stop': 'fixed at entry; ATR14 is simple mean true range on closed native 4h, not Wilder/RMA or trailing',
            'perp_size': config.perp_size,
            'allocation': 'notional caps on min(initial capital, equity); fixed Perp weights with optional 2/3 coin budget before shared constraints; unused stays cash; Spot ATR multiplier; no rebalance'},
        'limitations': sorted(book.limitations | {'research_only_not_activation_gate', 'not_current_jev_confidence_replay',
            'ex_post_not_independent_holdout', 'llm_not_called', 'intrabar_portfolio_dd_and_stop_timing_unknown',
            'funding_before_stop_detection_can_differ_from_intrabar_execution', 'assumed_cash_charged_slippage',
            'ideal_fractional_fills_without_partial_fills_or_historical_instrument_filters',
            'no_exact_liquidation_or_verified_historical_demo_execution', 'risk_gaps_and_exit_costs_can_overshoot'}),
        'v1_reference': None, 'equity_curve': book.curve, 'trades': book.trades, 'events': book.events}
    if isinstance(book, PerpDailyBook):
        report['summary']['perp_daily'] = daily_summary(book, valid)
        perp_curve = [{**row, 'equity_known': row['perp_equity']} for row in book.curve]
        report['summary']['perp_daily']['six_month_periods'] = continuous_periods(
            config.model_copy(update={'capital': book.daily.initial}), perp_curve)
        report['methodology']['perp_daily'] = {
            'policy': config.perp_daily_policy, 'initial_equity': str(book.daily.initial),
            'capital_basis': 'separate virtual Perp sleeve; initial capital times perp_cap, not margin or Spot PnL',
            'loss_pct': 3, 'target_pct': {'target3': 3, 'target5': 5}.get(config.perp_daily_policy),
            'trailing_arm_pct': 3, 'trailing_giveback_percentage_points': 1,
            'day_start': 'last observed sleeve equity before UTC day boundary; overnight PnL is differenced',
            'daily_resume': 'next UTC day AND Perp flat AND parent safe; never same tick as flatten',
            'fills': 'close/funding-triggered flatten at next contract open; open-triggered flatten at that open',
            'orders': 'opening requests blocked while locked; no exchange pending-order simulation',
            'priority': 'parent insolvency/DD/daily loss before Perp loss/profit/trailing',
        }
        report['limitations'] = sorted(set(report['limitations']) | {
            'four_hour_sampling_not_intraday_jev_llm_execution', 'virtual_perp_sleeve_not_segregated_exchange_wallet',
            'profit_target_not_guaranteed_after_exit_costs_or_gap'})
    return report
