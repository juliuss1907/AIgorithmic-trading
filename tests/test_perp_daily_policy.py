from datetime import timedelta
from decimal import Decimal

import pytest

from intraday.replay_v2.historical_mixed import HistoricalConfig
from intraday.replay_v2.perp_daily import PerpDailyBook, PerpDailyState
from test_historical_mixed_research import START, WIDTH


def state(policy='target3'):
    return PerpDailyState(Decimal(300), START, policy)


def book(policy='target3'):
    cfg = HistoricalConfig(start=START, end=START+2*timedelta(days=1),
                           perp_daily_policy=policy, trend_filter=False, perp_stop='fixed-5pct')
    ledger = PerpDailyBook(cfg)
    marks = {s: Decimal(100) for s in cfg.weights}
    ledger.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
    return ledger, marks


@pytest.mark.parametrize('policy,equity,reason', [
    ('none', '291', 'perp_daily_loss'), ('target3', '309', 'perp_daily_profit'),
    ('target5', '315', 'perp_daily_profit'), ('target3', '308.99', None),
])
def test_exact_daily_thresholds(policy, equity, reason):
    daily = state(policy)
    daily.observe(START, Decimal(equity))
    assert daily.evaluate(START) == reason
    assert daily.locked == bool(reason)


def test_trailing_arms_at_three_percent_and_peak_never_decreases():
    daily = state('trailing')
    for amount in ('308.99', '309', '313.5', '312'):
        daily.observe(START, Decimal(amount))
        assert daily.evaluate(START) is None
    assert daily.armed and daily.peak_return == Decimal('.045')
    daily.observe(START, Decimal('310.5'))
    assert daily.evaluate(START) == 'perp_daily_trailing'
    daily.observe(START, Decimal(315))
    assert daily.evaluate(START) is None and daily.locked


def test_next_day_baseline_carries_equity_not_total_lifetime_pnl():
    daily = state('none')
    daily.observe(START, Decimal(306))
    daily.advance_day(START+timedelta(days=1))
    daily.observe(START+timedelta(days=1), Decimal(309))
    assert daily.day_start == 306
    assert daily.daily_return == Decimal(3)/306
    assert not daily.armed


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '0', '-1'])
def test_invalid_capital_and_nonfinite_samples_are_rejected(value):
    with pytest.raises(ValueError):
        PerpDailyState(Decimal(value), START, 'none')
    if value in ('NaN', 'Infinity'):
        with pytest.raises(ValueError):
            state().observe(START, Decimal(value))


def test_perp_ledger_includes_entry_exit_costs_funding_and_not_spot():
    ledger, marks = book('none')
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    assert ledger.perp_equity() == Decimal('299.85')
    ledger.settle_funding('BTCUSDT', START, Decimal('.001'), Decimal(100))
    ledger.perp_marks['BTCUSDT'] = Decimal(110)
    assert ledger.perp_equity() == Decimal('314.70')
    ledger.enter_batch(START, marks, {'SOLUSDT': Decimal(1)})
    marks['SOLUSDT'] = Decimal(200)
    assert ledger.perp_equity() == Decimal('314.70')
    ledger.close_perp('BTCUSDT', START+WIDTH, Decimal(110), 'test_exit')
    assert ledger.perp_equity() == Decimal('314.535')
    assert ledger.daily.cash == 300+ledger.trades[-1]['net_pnl']


def test_stop_only_closes_one_position_and_net_pnl_already_covers_losses():
    ledger, marks = book('none')
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.enter_perp('ETHUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.perp_marks['ETHUSDT'] = Decimal(110)
    ledger.close_perp('BTCUSDT', START+WIDTH, Decimal(95), 'contract_stop_detected_at_close')
    ledger.enforce_risk(START+WIDTH, marks)
    assert ledger.perps['ETHUSDT'].quantity and not ledger.daily.locked
    assert ledger.daily.daily_return > 0


def test_perp_lock_does_not_lock_spot_and_resume_requires_next_day_and_flat():
    ledger, marks = book()
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.perp_marks['BTCUSDT'] = Decimal(107)
    ledger.enforce_risk(START+WIDTH, marks)
    assert ledger.daily.locked and not ledger.halted
    assert not ledger.enter_perp('ETHUSDT', START+WIDTH, marks, Decimal(100), 1, Decimal('.05'))
    ledger.enter_batch(START+WIDTH, marks, {'SOLUSDT': Decimal(1)})
    assert ledger.positions['SOLUSDT'].quantity
    next_day = START+timedelta(days=1)
    ledger.advance_day(next_day)
    ledger.maybe_resume_perp(next_day)
    assert ledger.daily.locked  # Not flat yet.
    ledger.close_perp('BTCUSDT', next_day, Decimal(107), ledger.daily.reason)
    ledger.maybe_resume_perp(next_day)
    assert ledger.daily.locked  # No same-tick reopen after delayed flatten.
    ledger.maybe_resume_perp(next_day+WIDTH)
    assert not ledger.daily.locked
