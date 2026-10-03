from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from test_intraday_timeframe_engine import inputs


def test_common_grid_audit_is_passive_and_reconciles_cash(tmp_path):
    from intraday.replay_v2.intraday_engine import simulate_intraday
    from intraday.replay_v2.intraday_audit import audit_drawdown
    args = inputs(tmp_path)
    report = simulate_intraday(*args)
    original = deepcopy(report)
    audit, curve = audit_drawdown(report, args[1], args[-1])
    assert report == original
    assert audit['samples'] == 65
    assert audit['final_equity'] == report['summary']['final_equity_known']
    assert audit['final_perp_equity'] == report['summary']['perp_daily']['final_equity']
    assert any(r['at'].startswith('2026-09-30T00:15:00') for r in curve)
    assert 'as-of' in audit['spot_valuation']


def test_immediate_entry_cost_halt_zero_duration_trade_has_no_open_audit_position(tmp_path):
    from intraday.replay_v2.intraday_engine import simulate_intraday
    from intraday.replay_v2.intraday_audit import audit_drawdown
    args = inputs(tmp_path)
    native = args[-1]
    rows = list(native['ETHUSDT']['trade1h'])
    rows[30] = rows[30].model_copy(update={'high':Decimal(101), 'close':Decimal(100)})
    rows[31] = rows[31].model_copy(update={'high':Decimal(105), 'close':Decimal(105)})
    native['ETHUSDT']['trade1h'] = tuple(rows)
    rows = list(native['ETHUSDT']['trade15m'])
    rows[3] = rows[3].model_copy(update={'high':Decimal(105), 'close':Decimal(105)})
    native['ETHUSDT']['trade15m'] = tuple(rows)
    rows = list(native['ETHUSDT']['mark15m'])
    rows[4] = rows[4].model_copy(update={'open':Decimal(93), 'low':Decimal(93)})
    native['ETHUSDT']['mark15m'] = tuple(rows)
    report = simulate_intraday(*args)
    zero = [t for t in report['trades'] if t['opened_at'] == t['closed_at']]
    assert len(zero) == 1 and zero[0]['symbol'] == 'ETHUSDT'
    assert zero[0]['exit_reason'] == 'perp_daily_loss'
    audit, _ = audit_drawdown(report, args[1], native)
    assert audit['final_equity'] == report['summary']['final_equity_known']
