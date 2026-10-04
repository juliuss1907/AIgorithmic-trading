from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from intraday.replay_v2.donchian_filter_book import FilterConfig, FilterBook, ATRTrail


START = datetime(2024, 10, 29, tzinfo=timezone.utc)


def book():
    b = FilterBook(FilterConfig(start=START, end=START+timedelta(days=2)))
    marks = {s:D(100) for s in b.weights}
    b.perp_marks = dict(marks)
    return b, marks


def test_matrix_config_fixed_allocations_and_weights():
    b, _ = book()
    assert b.realized_capital == {'spot':D(600), 'perp':D(400), 'unallocated':D(0)}
    assert b.weights == {'BTCUSDT':D('.4'), 'ETHUSDT':D('.3'), 'SOLUSDT':D('.3')}
    for override in [dict(leverage=2), dict(reserve=D('.1')), dict(entry_cap=D('.5')),
                     dict(entry_window=40), dict(filter_level=5), dict(daily_loss=D('.05')), dict(capital=D(2000))]:
        with pytest.raises(ValueError):
            FilterConfig(start=START, end=START+timedelta(days=1), **override)


def test_separate_sleeves_do_not_borrow_entry_fees_or_unrealized_gains():
    b, marks = book()
    b.enter_batch(START, marks, {s:D(1) for s in marks})
    principal = sum(p.quantity*p.entry_price for p in b.positions.values())
    assert principal <= b.realized_capital['spot']
    for s in marks:
        assert b.enter_perp(s, START, marks, D(100), -1, D('.05'))
    assert b.locked_margin <= b.realized_capital['perp']
    budget = b.perp_budget(marks)
    b.perp_marks = {s:D(10) for s in marks}
    assert b.perp_budget(marks) == budget
    assert b.free_cash >= 0


def test_short_only_and_realized_reinvestment_reconcile():
    b, marks = book()
    with pytest.raises(ValueError):
        b.enter_perp('BTCUSDT', START, marks, D(100), 1, D('.05'))
    b.enter_perp('BTCUSDT', START, marks, D(100), -1, D('.05'))
    b.settle_funding('BTCUSDT', START, D('.001'), D(100))
    b.close_perp('BTCUSDT', START+timedelta(hours=1), D(90), 'test')
    assert b.realized_capital['spot'] == 600
    assert abs(b.realized_capital['perp']-400-b.trades[-1]['net_pnl']) < D('1e-20')
    assert abs(sum(b.realized_capital.values())-b.cash) < D('1e-20')


def test_atr_trail_is_symmetric_and_never_loosens():
    long = ATRTrail(1, D(100), D(2))
    short = ATRTrail(-1, D(100), D(2))
    assert (long.stop, short.stop) == (94, 106)
    long.update(D(110), D(98), D(2))
    short.update(D(102), D(90), D(2))
    assert (long.stop, short.stop) == (104, 96)
    long.update(D(105), D(98), D(10))
    short.update(D(102), D(95), D(10))
    assert (long.stop, short.stop) == (104, 96)


def test_only_parent_daily_lock_then_next_utc_day_flat_resume():
    b, marks = book()
    b.enter_perp('BTCUSDT', START, marks, D(100), -1, D('.1'))
    b.perp_marks['BTCUSDT'] = D(120)
    b.enforce_risk(START+timedelta(hours=1), marks)
    assert b.halted and b.halt_reason == 'daily_loss_limit'
    assert b.perps['BTCUSDT'].quantity  # Locked now; engine fills next available open.
    b.close_perp('BTCUSDT', START+timedelta(hours=1, minutes=15), D(120), 'daily_loss_limit')
    b.enforce_risk(START+timedelta(hours=1, minutes=15), marks)
    b.advance_day(START+timedelta(days=1))
    b.maybe_resume(START+timedelta(days=1), marks)
    assert not b.halted
    assert b.day_start == b.equity(marks)


def test_peak_drawdown_above_ten_percent_is_observed_not_terminal_halt():
    b,marks = book()
    b.cash = b.day_start = D(850)
    b.realized_capital['spot'] = D(450)
    b.enforce_risk(START,marks)
    assert b.max_drawdown == D('.15')
    assert not b.halted
