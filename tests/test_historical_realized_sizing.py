from decimal import Decimal as D

import pytest

from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical
from intraday.replay_v2.perp_daily import PerpDailyBook
from test_historical_mixed_research import START, WIDTH, fixture_inputs


def realized_book():
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False,
                           capital_growth='realized', perp_daily_policy='none')
    book = PerpDailyBook(cfg)
    marks = {s: D(100) for s in cfg.weights}
    book.perp_marks = {s: D(100) for s in cfg.perp_weights}
    return book, marks


def test_spot_unrealized_changes_risk_not_realized_budget():
    book, marks = realized_book()
    book.enter_batch(START, marks, {'BTCUSDT': D(1)})
    budget = book.spot_budget(marks)
    quantity = book.positions['BTCUSDT'].quantity
    marks['BTCUSDT'] = D(150)
    assert book.spot_budget(marks) == budget
    assert book.equity(marks) > 1000
    marks['BTCUSDT'] = D(50)
    assert book.spot_budget(marks) == budget
    assert book.positions['BTCUSDT'].quantity == quantity
    assert book.budget_exposures(marks)[0] == quantity*100
    book.observe(START, marks)
    assert D(book.curve[-1]['spot_realized_capital']) == budget
    assert D(book.events[0]['market_sizing_capital']) == 600


def test_perp_realized_profit_only_grows_perp_and_reconciles_costs_once():
    book, marks = realized_book()
    spot = book.spot_budget(marks)
    book.enter_perp('BTCUSDT', START, marks, D(100), 1, D('.05'))
    assert book.perp_budget(marks) == D('299.85')
    book.perp_marks['BTCUSDT'] = D(120)
    assert book.perp_budget(marks) == D('299.85')
    book.settle_funding('BTCUSDT', START, D('.001'), D(100))
    assert book.perp_budget(marks) == D('299.70')
    book.close_perp('BTCUSDT', START, D(110), 'test')
    assert book.perp_budget(marks) == 300+book.trades[-1]['net_pnl']
    assert book.perp_budget(marks) == book.daily.cash
    assert book.spot_budget(marks) == spot
    assert book.allocation_base(marks) == book.cash


@pytest.mark.parametrize('price', [D(110), D(90)])
def test_spot_realization_never_transfers_to_perp(price):
    book, marks = realized_book()
    book.enter_batch(START, marks, {'BTCUSDT': D(1)})
    assert book.spot_budget(marks) == D('599.64')
    marks['BTCUSDT'] = price
    book.close('BTCUSDT', START, price, 'test')
    assert book.spot_budget(marks) == 600+book.trades[-1]['net_pnl']
    assert book.perp_budget(marks) == 300
    assert book.allocation_base(marks) == book.cash


def test_negative_sleeve_blocks_new_entries_without_negative_budget():
    book, marks = realized_book()
    book.realized_capital['perp'] = D(-1)
    assert book.perp_target('BTCUSDT', marks)[1:] == (D(0), D(0))
    assert not book.enter_perp('BTCUSDT', START, marks, D(100), 1, D('.05'))


def test_realized_replay_reconciles_sleeves_and_has_distinct_identity():
    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False,
                           capital_growth='realized', perp_daily_policy='none')
    report = simulate_historical(cfg, *fixture_inputs(cfg))
    capital = report['summary']['realized_sizing']
    assert sum(capital['final_capital'].values()) == pytest.approx(report['summary']['final_equity_known'])
    assert report['config']['capital_growth'] == 'realized'
    old = simulate_historical(cfg.model_copy(update={'capital_growth': 'capped'}), *fixture_inputs(cfg))
    assert old['result_id'] != report['result_id']
    assert 'realized_sizing' not in old['summary']
