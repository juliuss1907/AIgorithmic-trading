from datetime import timedelta
from decimal import Decimal as D

import pytest

from test_historical_mixed_research import START, fixture_inputs
from test_intraday_timeframe_data import native_bundle


def inputs(tmp_path, policy='off', side=-1):
    from intraday.replay_v2.short_reserve_book import ShortReserveConfig
    from intraday.replay_v2.intraday_data import decode_inputs
    cfg = ShortReserveConfig(start=START, end=START+timedelta(hours=8),
        trend_filter=False, reserve_policy=policy)
    spot, daily, perp, funding = fixture_inputs(cfg, side=side)
    native = decode_inputs(cfg, native_bundle(cfg, tmp_path), perp)
    return cfg, spot, daily, perp, funding, native


def test_only_h4_short_signals_margin_sizing_and_window_end_no_reserve(tmp_path):
    from intraday.replay_v2.short_reserve_engine import simulate_short_reserve
    args = inputs(tmp_path, 'restore-and-repay')
    r = simulate_short_reserve(*args)
    assert r == simulate_short_reserve(*args)
    assert r['summary']['perp_sides']['long']['closed_trades'] == 0
    assert r['summary']['perp_sides']['short']['closed_trades'] == 2
    assert not r['reserve_transfers']
    assert all(t['opened_at'] == START.isoformat() for t in r['trades'])
    assert all(t['exit_reason'] == 'window_end' for t in r['trades'])
    entries = [e for e in r['events'] if e['kind'] == 'entry']
    assert all(e['stop_price'] == '110.00' and e['signal_interval'] == '4h' for e in entries)
    assert sum(D(e['notional']) for e in entries) > 400
    parent_times = {row['at'] for row in r['equity_curve']}
    assert (START+timedelta(minutes=15)).isoformat() not in parent_times


def test_long_breakout_cannot_enter_short(tmp_path):
    from intraday.replay_v2.short_reserve_engine import simulate_short_reserve
    r = simulate_short_reserve(*inputs(tmp_path, side=1))
    assert not r['trades']


@pytest.mark.parametrize('policy', ['off', 'restore-and-repay'])
def test_m15_loss_flatten_preserves_lock_and_flow_neutral_daily_loss(tmp_path, policy):
    from intraday.replay_v2.short_reserve_engine import simulate_short_reserve
    args = inputs(tmp_path, policy)
    rows = list(args[-1]['BTCUSDT']['mark15m'])
    rows[1] = rows[1].model_copy(update={'open':D(110), 'high':D(110)})
    args[-1]['BTCUSDT']['mark15m'] = tuple(rows)
    r = simulate_short_reserve(*args)
    assert all(t['closed_at'] == (START+timedelta(minutes=15)).isoformat() for t in r['trades'])
    assert r['summary']['perp_daily']['loss_halts'] == 1
    assert not any(e['kind'] == 'perp_daily_resume' for e in r['events'])
    assert bool(r['reserve_transfers']) == (policy != 'off')
    assert r['summary']['reserve']['reconciled']


def test_contract_stop_detected_m15_close(tmp_path):
    from intraday.replay_v2.short_reserve_engine import simulate_short_reserve
    args = inputs(tmp_path)
    rows = list(args[-1]['BTCUSDT']['trade15m'])
    rows[0] = rows[0].model_copy(update={'high':D(111)})
    args[-1]['BTCUSDT']['trade15m'] = tuple(rows)
    r = simulate_short_reserve(*args)
    btc = next(t for t in r['trades'] if t['symbol'] == 'BTCUSDT')
    assert btc['exit_reason'] == 'contract_stop_detected_at_close'
    assert D(btc['exit_price']) == 110
    assert btc['closed_at'] == (START+timedelta(minutes=15)-timedelta(milliseconds=1)).isoformat()
