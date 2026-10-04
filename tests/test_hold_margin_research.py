from datetime import timedelta
from decimal import Decimal as D

import pytest

from test_historical_mixed_research import START
from test_short_reserve_engine import inputs


def test_short_one_x_uses_margin_as_notional_and_restores_reserve(tmp_path):
    from intraday.replay_v2.hold_margin_research import AllocationConfig, simulate_allocation
    args = inputs(tmp_path, 'restore-and-repay')
    cfg = AllocationConfig(start=START, end=START+timedelta(hours=8), trend_filter=False,
        leverage=1, reserve_policy='restore-and-repay')
    rows = list(args[-1]['BTCUSDT']['mark15m'])
    rows[1] = rows[1].model_copy(update={'open':D(120), 'high':D(120)})
    args[-1]['BTCUSDT']['mark15m'] = tuple(rows)
    r = simulate_allocation(cfg, *args[1:])
    entries = [e for e in r['events'] if e['kind'] == 'entry']
    assert sum(D(e['notional']) for e in entries) < 300
    assert all(t['side'] == 'short' for t in r['trades'])
    assert r['summary']['reserve']['drawn'] > 0


@pytest.mark.parametrize('side', [1,-1])
def test_hold_plus_perp_supports_both_sides_but_never_sells_hold_until_end(tmp_path, side):
    from intraday.replay_v2.hold_margin_research import AllocationConfig, simulate_allocation
    args = inputs(tmp_path, side=side)
    cfg = AllocationConfig(start=START, end=START+timedelta(hours=8), trend_filter=False,
        spot_strategy='buy-and-hold', perp_direction='long-short')
    # A parent daily breach should flatten Perp, never the buy-and-hold Spot.
    spot = dict(args[1])
    for s, candles in spot.items():
        rows = list(candles)
        rows[-2] = rows[-2].model_copy(update={'low':D(70), 'close':D(70)})
        rows[-1] = rows[-1].model_copy(update={'open':D(70), 'low':D(70), 'close':D(70)})
        spot[s] = tuple(rows)
    r = simulate_allocation(cfg, spot, *args[2:])
    perp = [t for t in r['trades'] if t['market'] == 'perp']
    held = [t for t in r['trades'] if t['market'] == 'spot']
    assert len(held) == 5 and all(t['exit_reason'] == 'window_end' for t in held)
    assert perp and all(t['side'] == ('long' if side == 1 else 'short') for t in perp)
    assert r['summary']['daily_pause_count'] > 0
    assert not r['reserve_transfers']


def test_full_capital_hold_has_only_two_fills_per_coin_and_no_risk_exit(tmp_path):
    from intraday.replay_v2.hold_margin_research import simulate_buy_hold
    args = inputs(tmp_path)
    r = simulate_buy_hold(args[0].start, args[0].end, args[1], args[0].weights)
    assert len(r['trades']) == 5
    assert all(t['exit_reason'] == 'window_end' for t in r['trades'])
    assert r['config']['entry_cap'] == '1'
    assert r['summary']['final_equity_known'] < 1000
    assert r['summary']['economic_check_only'] == 'not_applicable_benchmark'
    entered = sum(D(e['notional']) for e in r['events'] if e['kind'] == 'entry')
    assert abs(entered*D('1.0015')-1000) < D('1e-18')
    from intraday.replay_v2.intraday_audit import audit_drawdown
    audit, _ = audit_drawdown(r, args[1], args[-1])
    assert audit['final_equity'] == r['summary']['final_equity_known']
    assert audit['perp_max_drawdown_pct'] == 0
    from intraday.replay_v2.artifacts import publish_report, read_report
    saved = publish_report(tmp_path/'benchmark', r)
    assert read_report(tmp_path/'benchmark', saved['run_id'])['summary'] == r['summary']


def test_hold_parent_can_resume_with_spot_open_but_not_erase_new_day_loss():
    from intraday.replay_v2.hold_margin_research import AllocationBook, AllocationConfig
    cfg = AllocationConfig(start=START, end=START+timedelta(days=3), spot_strategy='buy-and-hold')
    b = AllocationBook(cfg)
    marks = {s:D(100) for s in cfg.weights}
    b.perp_marks = {s:D(100) for s in cfg.perp_weights}
    b.enter_batch(START, marks, {s:D(1) for s in cfg.weights})
    b.halted, b.halt_reason = True, 'daily_loss_limit'
    b.pause_until = START+timedelta(days=1)
    b.last_equity = D(1000)
    b.advance_day(START+timedelta(days=1))
    low_marks = {s:D(80) for s in cfg.weights}
    b.maybe_resume(START+timedelta(days=1), low_marks)
    assert b.halted and b.pause_until == START+timedelta(days=2)
    b.last_equity = b.equity(low_marks)
    b.advance_day(START+timedelta(days=2))
    b.maybe_resume(START+timedelta(days=2), low_marks)
    assert not b.halted and not b.flat
