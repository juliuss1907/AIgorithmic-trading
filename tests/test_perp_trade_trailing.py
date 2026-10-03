from datetime import timedelta
from decimal import Decimal as D

import pytest

from intraday.replay_v2.historical_mixed import HistoricalConfig, PerpObservation, open_exits
from test_historical_mixed_research import START, WIDTH, bars, fixture_inputs


def trailing_book(side=1):
    from intraday.replay_v2.trade_trailing import TradeTrailingBook
    cfg = HistoricalConfig(start=START, end=START+4*WIDTH, trend_filter=False,
                           perp_stop='atr14-3x', perp_daily_policy='none',
                           perp_trade_exit='net-trailing-3pp', drawdown_policy='observe-only')
    book = TradeTrailingBook(cfg)
    marks = {s: D(100) for s in cfg.weights}
    book.perp_marks = {s: D(100) for s in cfg.perp_weights}
    book.enter_perp('BTCUSDT', START, marks, D(100), side, D('.06'))
    return book, marks


def test_continuous_trailing_exact_boundaries_and_never_lowers_floor():
    from intraday.replay_v2.trade_trailing import TradeTrailingState
    state = TradeTrailingState()
    state.observe(D('.029999'))
    assert state.floor is None
    state.observe(D('.03'))
    assert state.floor == 0
    state.observe(D('.045'))
    assert state.floor == D('.015')
    state.observe(D('.06'))
    assert state.floor == D('.03')
    state.observe(D('.04'))
    assert state.floor == D('.03') and not state.breached(D('.04'))
    assert state.breached(D('.03'))


@pytest.mark.parametrize('side,profitable,higher,fallback', [(1, '104', '107', '103'), (-1, '96', '93', '97')])
def test_net_trailing_is_symmetric_and_survives_utc_day(side, profitable, higher, fallback):
    book, marks = trailing_book(side)
    def observe(at, price):
        bar = bars(at-WIDTH, 1, D(price))[0]
        book.observe_trade_trailing(at, {'BTCUSDT': bar})
    observe(START+WIDTH, profitable)
    state = book.trade_trailing['BTCUSDT']
    assert state.floor is not None
    observe(START+2*WIDTH, higher)
    floor = state.floor
    book.advance_day(START+timedelta(days=1))
    assert state.floor == floor and not book.daily.locked
    observe(START+timedelta(days=1)+WIDTH, fallback)
    assert book.perp_exit_reason('BTCUSDT', D(fallback), START+timedelta(days=1)+WIDTH) == 'perp_trade_trailing'


def test_funding_and_estimated_exit_costs_are_in_net_return_once():
    book, _ = trailing_book()
    p = book.perps['BTCUSDT']
    book.settle_funding('BTCUSDT', START+WIDTH, D('.001'), D(100))
    expected = (p.quantity*4-p.entry_fee-p.entry_slip-p.funding_paid-abs(p.quantity)*104*D('.001'))/(abs(p.quantity)*100)
    assert book.projected_trade_return('BTCUSDT', D(104)) == expected
    assert expected < D('.04')


def test_open_gap_closes_at_contract_open_without_daily_lock_or_same_tick_reentry():
    book, marks = trailing_book()
    book.observe_trade_trailing(START+WIDTH, {'BTCUSDT': bars(START, 1, D(107))[0]})
    closed = set()
    open_exits(book, START+WIDTH, {}, {'BTCUSDT': bars(START+WIDTH, 1, D(102))[0]}, {},
               {'BTCUSDT': PerpObservation(1, False, False, D(2))}, closed)
    assert book.trades[-1]['exit_reason'] == 'perp_trade_trailing'
    assert D(book.trades[-1]['exit_price']) == D(102)
    assert ('perp', 'BTCUSDT') in closed and not book.daily.locked
    assert book.trades[-1]['trailing_floor_return'] > 0


def test_close_observation_does_not_use_intrabar_high_to_arm():
    book, _ = trailing_book()
    bar = bars(START, 1)[0].model_copy(update={'high': D(120), 'low': D(90)})
    book.observe_trade_trailing(START+WIDTH, {'BTCUSDT': bar})
    assert book.trade_trailing['BTCUSDT'].floor is None


@pytest.mark.parametrize('change', [{'perp_daily_policy': 'target5'}, {'include_perp': False}, {'perp_stop': 'fixed-5pct'}])
def test_trailing_rejects_incompatible_research_configs(change):
    payload = dict(start=START, end=START+WIDTH, perp_trade_exit='net-trailing-3pp',
                   perp_stop='atr14-3x', perp_daily_policy='none')
    with pytest.raises(ValueError):
        HistoricalConfig(**{**payload, **change})


def test_full_replay_latches_close_breach_and_fills_next_open_even_after_bounce():
    from intraday.replay_v2.historical_mixed import simulate_historical
    from intraday.replay_v2.contracts import FundingSettlement
    book, _ = trailing_book()
    cfg = book.config.model_copy(update={'capital_growth': 'realized'})
    spot, daily, perp, funding = fixture_inputs(cfg)
    for s, data in perp.items():
        paths = []
        for i, (opened, closed) in enumerate([(100, 104), (104, 107), (107, 103), (104, 104)]):
            paths.append(bars(START+i*WIDTH, 1, D(opened))[0].model_copy(update={
                'close': D(closed), 'high': D(max(opened, closed)+1), 'low': D(min(opened, closed)-1)}))
        data['trade'] = data['trade'][:-4]+tuple(paths)
        data['mark'] = tuple(paths)
        funding[s] = funding[s].model_copy(update={'settlements': tuple(
            FundingSettlement(at=START+i*WIDTH, rate=0, mark=100) for i in (0, 2))})
    report = simulate_historical(cfg, spot, daily, perp, funding)
    exits = [t for t in report['trades'] if t['exit_reason'] == 'perp_trade_trailing']
    assert len(exits) == 2 and report['status'] == 'complete'
    assert all(t['closed_at'] == (START+3*WIDTH).isoformat() and t['exit_price'] == '104' for t in exits)
    assert all(t['trailing_triggered_at'] == (START+3*WIDTH-timedelta(milliseconds=1)).isoformat() for t in exits)
    assert not [e for e in report['events'] if e['kind'] == 'entry' and e['at'] == exits[0]['closed_at']]
    assert report['summary']['perp_daily']['profit_halts'] == 0
    assert report == simulate_historical(cfg, spot, daily, perp, funding)


@pytest.mark.parametrize('reason', ['donchian_exit', 'contract_stop_gap', 'perp_daily_loss', 'max_drawdown'])
def test_other_exits_have_priority_and_do_not_reset_trailing(reason):
    book, marks = trailing_book()
    book.observe_trade_trailing(START+WIDTH, {'BTCUSDT': bars(START, 1, D(107))[0]})
    price = D(105)
    obs = PerpObservation(0, reason == 'donchian_exit', False, D(2))
    if reason == 'contract_stop_gap':
        price = D(90)
    elif reason == 'perp_daily_loss':
        book.daily.locked, book.daily.reason = True, reason
        price = D(102)  # Also breaches trailing, but daily flatten wins.
    elif reason == 'max_drawdown':
        book.halted, book.halt_reason = True, reason
        price = D(102)
    open_exits(book, START+WIDTH, {}, {'BTCUSDT': bars(START+WIDTH, 1, price)[0]}, {}, {'BTCUSDT': obs}, set())
    assert book.trades[-1]['exit_reason'] == reason
    assert book.trades[-1]['trailing_floor_return'] is not None
