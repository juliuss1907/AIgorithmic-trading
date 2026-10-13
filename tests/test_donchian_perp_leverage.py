from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from intraday.replay_v2.donchian_perp_leverage import PerpLeverageBook, PerpLeverageConfig, perp_cases
from intraday.replay_v2.donchian_filter_book import ATRTrail

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def book(leverage=5):
    cfg = PerpLeverageConfig(start=START, end=START+timedelta(days=2), leverage=leverage)
    b = PerpLeverageBook(cfg)
    marks = {s: D(100) for s in cfg.weights}
    b.perp_marks = dict(marks)
    return b, marks


@pytest.mark.parametrize('change', [dict(leverage=3), dict(entry_cap=D('.1')), dict(perp_cap=D('.9')),
    dict(daily_loss=D('.04')), dict(hard_stop_pct=D('.05')), dict(maintenance_margin_rate=D('.005')),
    dict(filter_level=3), dict(perp_trade_exit='net-trailing-3pp'), dict(capital=D(2000))])
def test_new_config_locks_the_specification(change):
    with pytest.raises(ValueError):
        PerpLeverageConfig(start=START, end=START+timedelta(days=1), **change)


def test_control_and_treatments_restore_from_json_and_keep_exact_thirds():
    cases = perp_cases(START, START+timedelta(days=1))
    assert [n for n, _ in cases] == ['ETH-NEAR-SOL-H4', 'PERP1X', 'PERP5X']
    for _, cfg in cases[1:]:
        assert type(cfg).model_validate_json(cfg.model_dump_json()) == cfg
        assert sum(cfg.weights.values()) == 1 and cfg.weights == cfg.perp_weights
        assert max(cfg.weights.values())-min(cfg.weights.values()) < D('1e-27')


@pytest.mark.parametrize('leverage,side', [(1, 1), (1, -1), (5, 1), (5, -1)])
def test_margin_funding_and_hand_calculated_pnl(leverage, side):
    b, marks = book(leverage)
    target = D(300)*leverage
    assert b.enter_perp('ETHUSDT', START, marks, D(100), side, D('.1'), approved_target=target)
    p = b.perps['ETHUSDT']
    assert p.quantity == side*D(3)*leverage and p.margin == 300
    assert b.free_cash == 1000-target*D('.001')-300
    b.settle_funding('ETHUSDT', START, D('.001'), D(100))
    assert b.funding_paid == side*target*D('.001')
    exit_price = D(100)+side*D(2)
    b.close_perp('ETHUSDT', START+timedelta(hours=1), exit_price, 'donchian_exit')
    expected = D(6)*leverage-target*D('.001')-D(3)*leverage*exit_price*D('.001')-side*target*D('.001')
    assert abs(b.trades[-1]['net_pnl']-expected) < D('1e-20')
    assert abs(b.cash-1000-expected) < D('1e-20')
    assert sum(b.realized_capital.values()) == b.cash and b.locked_margin == 0


def test_multiple_positions_bound_margin_and_charge_notional_costs():
    b, marks = book()
    for symbol, side in [('ETHUSDT', 1), ('NEARUSDT', -1), ('SOLUSDT', 1)]:
        assert b.enter_perp(symbol, START, marks, D(100), side, D('.1'))
        assert b.locked_margin <= b.realized_capital['perp']+D('1e-20')
        assert b.free_cash >= -D('1e-20')
    assert b.locked_margin > 990 and b.fees+b.slippage > D('4.9')
    assert abs(b.locked_margin*5-sum(abs(p.quantity)*p.entry_price for p in b.perps.values())) < D('1e-20')
    budget = b.perp_budget(marks)
    b.perp_marks = dict.fromkeys(marks, D(120))
    assert b.perp_budget(marks) == budget


@pytest.mark.parametrize('side,liquidation', [(1, D('80.4')), (-1, D('119.6'))])
def test_liquidation_forfeits_margin_reconciles_and_continues(side, liquidation):
    b, marks = book()
    b.enter_perp('ETHUSDT', START, marks, D(100), side, D('.1'), approved_target=D(1500))
    p = b.perps['ETHUSDT']
    assert b.liquidation_price(p) == liquidation
    cash_before, margin = b.cash, p.margin
    b.perp_marks['ETHUSDT'] = liquidation
    b.check_isolated_collateral(START+timedelta(hours=1))
    assert b.flat and not b.halted and not b.limitations
    assert b.cash == cash_before-margin and b.locked_margin == 0
    t = b.trades[-1]
    assert D(t['exit_price']) == liquidation and t['exit_reason'] == 'liquidation'
    assert abs(t['net_pnl']-(b.cash-D(1000))) < D('1e-20')
    assert t['liquidation_penalty'] > 0 and sum(b.realized_capital.values()) == b.cash
    assert any(e['kind'] == 'liquidation' for e in b.events)
    b.enforce_risk(START+timedelta(hours=1), marks)
    assert b.halt_reason == 'daily_loss_limit'
    b.advance_day(START+timedelta(days=1), marks)
    b.maybe_resume(START+timedelta(days=1), marks)
    assert b.enter_perp('NEARUSDT', START+timedelta(days=1), marks, D(100), -side, D('.1'))


def test_one_x_retains_unsupported_liquidation():
    b, marks = book(1)
    b.enter_perp('ETHUSDT', START, marks, D(100), -1, D('.1'))
    b.perp_marks['ETHUSDT'] = D(250)
    b.check_isolated_collateral(START)
    assert b.halted and b.halt_reason == 'unsupported_liquidation'


@pytest.mark.parametrize('side,initial,tightened', [(1, D(90), D(103)), (-1, D(110), D(97))])
def test_ten_percent_stop_is_symmetric_and_only_tightens(side, initial, tightened):
    trail = ATRTrail(side, D(100), D(5), 3, D('.1'))
    assert trail.stop == initial
    trail.update(D(106), D(94), D(1))
    assert trail.stop == tightened
    trail.update(D(101), D(99), D(10))
    assert trail.stop == tightened
