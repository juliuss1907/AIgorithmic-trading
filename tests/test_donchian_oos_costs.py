from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from intraday.replay_v2.donchian_filter_book import FilterBook
from intraday.replay_v2.donchian_oos_config import OOSConfig


def make(multiplier):
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    config = OOSConfig(start=start, end=start+timedelta(days=1), cost_multiplier=multiplier)
    book = FilterBook(config)
    marks = {s: D(100) for s in book.weights}
    book.perp_marks = dict(marks)
    return book, marks, start


@pytest.mark.parametrize('multiplier', [1, 2])
def test_fill_costs_and_capital_use_instance_rates(multiplier):
    b, marks, start = make(multiplier)
    b.enter_batch(start, marks, {'BTCUSDT': D(1)})
    p = b.positions['BTCUSDT']
    assert p.entry_fee == p.quantity*p.entry_price*D('.001')*multiplier
    assert p.entry_slip == p.quantity*p.entry_price*D('.0005')*multiplier
    b.close('BTCUSDT', start, D(100), 'test')
    assert b.trades[-1]['exchange_fee'] == 2*p.entry_fee
    assert b.trades[-1]['slippage_cost'] == 2*p.entry_slip
    assert b.enter_perp('BTCUSDT', start, marks, D(100), -1, D('.05'))
    p = b.perps['BTCUSDT']
    q = abs(p.quantity)
    b.settle_funding('BTCUSDT', start, D('.001'), D(100))
    b.close_perp('BTCUSDT', start, D(100), 'test')
    t = b.trades[-1]
    assert t['exchange_fee'] == q*100*D('.001')*multiplier
    assert t['slippage_cost'] == q*100*D('.001')*multiplier
    assert t['funding_paid'] == -q*100*D('.001')
    assert abs(sum(b.realized_capital.values())-b.cash) < D('1e-18')


def test_stress_affects_sizing_without_global_leak():
    stress, marks, at = make(2)
    baseline, _, _ = make(1)
    for b in (stress, baseline):
        b.enter_batch(at, marks, {s:D(1) for s in marks})
        for s in marks:
            b.enter_perp(s, at, marks, D(100), -1, D('.05'))
        assert b.free_cash >= 0
        assert b.locked_margin <= b.realized_capital['perp']
    assert stress.positions['BTCUSDT'].quantity < baseline.positions['BTCUSDT'].quantity
    assert sum(abs(p.quantity) for p in stress.perps.values()) < sum(abs(p.quantity) for p in baseline.perps.values())
    for invalid in (0, 3, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            make(invalid)
