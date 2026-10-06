from datetime import timedelta
from decimal import Decimal

import pytest

from test_historical_mixed_research import START, fixture_inputs
from test_intraday_timeframe_data import native_bundle


def inputs(tmp_path, interval='1h', side=1):
    from intraday.replay_v2.intraday_book import IntradayConfig
    from intraday.replay_v2.intraday_data import decode_inputs
    cfg = IntradayConfig(start=START, end=START+timedelta(hours=8),
        trend_filter=False, perp_trailing_interval=interval)
    spot, daily, perp, funding = fixture_inputs(cfg)
    native = decode_inputs(cfg, native_bundle(cfg, tmp_path), perp)
    # A closed H1 breakout, deliberately absent in the active first H1 bar.
    for series in native.values():
        rows = list(series['trade1h'])
        rows[30] = rows[30].model_copy(update={
            'high' if side == 1 else 'low':Decimal(105 if side == 1 else 95),
            'close':Decimal(105 if side == 1 else 95)})
        series['trade1h'] = tuple(rows)
    return cfg, spot, daily, perp, funding, native


def test_h1_next_open_entries_atr_and_offline_reproduction(tmp_path):
    from intraday.replay_v2.intraday_engine import simulate_intraday
    args = inputs(tmp_path)
    report = simulate_intraday(*args)
    repeated = simulate_intraday(*args)
    assert report == repeated
    assert report['evaluator_version'] == 'historical-perp-intraday-timeframes-v1.1'
    assert report['summary']['closed_trades'] == 2
    assert report['summary']['realized_sizing']['final_capital']['perp'] < 300
    entries = [e for e in report['events'] if e['kind'] == 'entry']
    assert all(e['at'] == START.isoformat() for e in entries)
    assert all(e['signal_interval'] == '1h' and e['stop_fraction'] != '.01' for e in entries)
    parent_times = {r['at'] for r in report['equity_curve']}
    assert (START+timedelta(minutes=15)).isoformat() not in parent_times
    assert (START+timedelta(minutes=15)).isoformat() in {r['at'] for r in report['perp_equity_curve']}


@pytest.mark.parametrize('side', [1,-1])
def test_m15_stop_touch_closes_single_position_at_close_not_h1_close(tmp_path, side):
    from intraday.replay_v2.intraday_engine import simulate_intraday
    cfg, spot, daily, perp, funding, native = inputs(tmp_path, side=side)
    rows = list(native['BTCUSDT']['trade15m'])
    rows[0] = rows[0].model_copy(update={'low' if side == 1 else 'high': Decimal(80 if side == 1 else 120)})
    native['BTCUSDT']['trade15m'] = tuple(rows)
    report = simulate_intraday(cfg, spot, daily, perp, funding, native)
    btc = next(t for t in report['trades'] if t['symbol'] == 'BTCUSDT')
    assert btc['exit_reason'] == 'contract_stop_detected_at_close'
    assert btc['side'] == ('long' if side == 1 else 'short')
    assert btc['closed_at'] == (START+timedelta(minutes=15)-timedelta(milliseconds=1)).isoformat()


def test_new_trailing_intervals_have_distinct_identities(tmp_path):
    from intraday.replay_v2.intraday_engine import simulate_intraday
    args = inputs(tmp_path)
    first = simulate_intraday(*args)
    second = simulate_intraday(args[0].model_copy(update={'perp_trailing_interval': '15m'}), *args[1:])
    assert first['result_id'] != second['result_id']
    assert first['inputs']['dataset_checksum'] == second['inputs']['dataset_checksum']


def test_m15_daily_loss_flattens_all_perps_without_parent_halt(tmp_path):
    from intraday.replay_v2.intraday_engine import simulate_intraday
    args = inputs(tmp_path)
    native = args[-1]
    rows = list(native['BTCUSDT']['mark15m'])
    rows[1] = rows[1].model_copy(update={'open': Decimal(93), 'low': Decimal(93)})
    native['BTCUSDT']['mark15m'] = tuple(rows)
    report = simulate_intraday(*args)
    assert report['summary']['perp_daily']['loss_halts'] == 1
    assert report['summary']['daily_pause_count'] == 0
    assert all(t['exit_reason'] == 'perp_daily_loss' and
               t['closed_at'] == (START+timedelta(minutes=15)).isoformat() for t in report['trades'])


def test_m15_trailing_latches_breach_then_fills_on_rebound_before_h1(tmp_path):
    from intraday.replay_v2.intraday_engine import simulate_intraday
    args = inputs(tmp_path)
    native = args[-1]
    rows = list(native['BTCUSDT']['trade1h'])
    rows[31] = rows[31].model_copy(update={'high': Decimal(107), 'close': Decimal(107)})
    rows[32] = rows[32].model_copy(update={'open': Decimal(107), 'high': Decimal(107), 'close': Decimal(107)})
    native['BTCUSDT']['trade1h'] = tuple(rows)
    rows = list(native['BTCUSDT']['trade15m'])
    for i, op, close in [(3,100,107), (4,107,103), (5,107,107), (6,107,107), (7,107,107)]:
        rows[i] = rows[i].model_copy(update={'open': Decimal(op), 'high': Decimal(107), 'close': Decimal(close)})
    native['BTCUSDT']['trade15m'] = tuple(rows)
    h1 = simulate_intraday(*args)
    m15 = simulate_intraday(args[0].model_copy(update={'perp_trailing_interval':'15m'}), *args[1:])
    first = next(t for t in h1['trades'] if t['symbol'] == 'BTCUSDT')
    second = next(t for t in m15['trades'] if t['symbol'] == 'BTCUSDT')
    assert first['closed_at'] == (START+timedelta(hours=2)).isoformat()
    assert second['closed_at'] == (START+timedelta(hours=1, minutes=15)).isoformat()
    assert second['trailing_triggered_at'] == (START+timedelta(hours=1, minutes=15)-timedelta(milliseconds=1)).isoformat()
    assert second['net_pnl'] > first['net_pnl']


def test_open_fill_loss_flattens_other_perps_at_same_open(tmp_path):
    from intraday.replay_v2.intraday_engine import simulate_intraday
    args = inputs(tmp_path)
    native = args[-1]
    rows = list(native['BTCUSDT']['trade1h'])
    rows[31] = rows[31].model_copy(update={'low':Decimal(94), 'close':Decimal(94)})
    rows[32] = rows[32].model_copy(update={'low':Decimal(94), 'open':Decimal(94)})
    native['BTCUSDT']['trade1h'] = tuple(rows)
    rows = list(native['BTCUSDT']['trade15m'])
    rows[3] = rows[3].model_copy(update={'low':Decimal(94), 'close':Decimal(94)})
    rows[4] = rows[4].model_copy(update={'low':Decimal(94), 'open':Decimal(94)})
    native['BTCUSDT']['trade15m'] = tuple(rows)
    report = simulate_intraday(*args)
    eth = next(t for t in report['trades'] if t['symbol'] == 'ETHUSDT')
    assert eth['exit_reason'] == 'perp_daily_loss'
    assert eth['closed_at'] == (START+timedelta(hours=1)).isoformat()
