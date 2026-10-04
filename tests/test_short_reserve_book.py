from datetime import timedelta
from decimal import Decimal as D

import pytest

from test_historical_mixed_research import START
from intraday.replay_v2.short_reserve_book import ShortReserveBook, ShortReserveConfig


def book(policy='restore-and-repay'):
    cfg = ShortReserveConfig(start=START, end=START+timedelta(days=2), reserve_policy=policy)
    b = ShortReserveBook(cfg)
    b.perp_marks = {s:D(100) for s in cfg.perp_weights}
    return b, {s:D(100) for s in cfg.weights}


def loss(b, value):
    b.cash -= value
    b.realized_capital['perp'] -= value
    b.daily.cash -= value


def test_transfer_restore_then_repay_never_creates_pnl_or_resets_lock():
    b, marks = book()
    loss(b, D(15))
    b.enforce_risk(START, marks)
    assert b.daily.locked
    b.rebalance_reserve(START, marks)
    assert b.cash == 985
    assert b.realized_capital == {'spot':D(600), 'perp':D(300), 'unallocated':D(85)}
    assert b.daily.daily_return == D('-.05')
    assert b.daily.locked and b.daily.max_drawdown == D('.05')
    loss(b, D(-8))
    b.rebalance_reserve(START+timedelta(hours=4), marks)
    assert b.realized_capital['perp'] == 300
    assert b.realized_capital['unallocated'] == 93
    assert b.cash == 993 and b.daily.capital_flows == 7


def test_reserve_empty_reuse_and_next_day_actual_baseline():
    b, marks = book()
    loss(b, D(150))
    b.rebalance_reserve(START, marks)
    assert b.realized_capital['perp'] == 250 and b.reserve_balance == 0
    loss(b, D(-100))
    b.rebalance_reserve(START+timedelta(hours=4), marks)
    assert b.reserve_balance == 50 and b.realized_capital['perp'] == 300
    loss(b, D(20))
    b.rebalance_reserve(START+timedelta(hours=8), marks)
    assert b.drawn == 120 and b.repaid == 50 and b.reserve_balance == 30
    b.advance_day(START+timedelta(days=1))
    b.daily.observe(START+timedelta(days=1), b.perp_equity())
    assert b.daily.day_start == 300 and b.daily.daily_return == 0


def test_live_positions_and_window_end_cannot_draw_reserve():
    b, marks = book()
    b.enter_perp('BTCUSDT', START, marks, D(100), -1, D('.10'))
    b.rebalance_reserve(START, marks)
    assert b.reserve_balance == 100
    b.close_perp('BTCUSDT', START+timedelta(hours=1), D(105), 'window_end')
    b.rebalance_reserve(b.config.end, marks)
    assert b.reserve_balance == 100


def test_margin_budget_uses_locked_principal_not_short_mark_profit():
    b, marks = book('off')
    assert b.enter_perp('BTCUSDT', START, marks, D(100), -1, D('.10'))
    assert b.locked_margin == 150 and abs(b.perps['BTCUSDT'].quantity)*100 == 300
    b.perp_marks['BTCUSDT'] = D(10)
    assert b.perp_target('ETHUSDT', marks)[2] <= D('299.4')
    assert b.enter_perp('ETHUSDT', START, marks, D(100), -1, D('.10'))
    assert b.locked_margin <= b.realized_capital['perp']
    assert b.free_cash >= b.reserve_balance


def test_short_only_and_fixed_contract_are_validated():
    b, marks = book()
    with pytest.raises(ValueError, match='short'):
        b.enter_perp('BTCUSDT', START, marks, D(100), 1, D('.10'))
    with pytest.raises(ValueError):
        ShortReserveConfig(start=START, end=START+timedelta(days=1), leverage=3)
