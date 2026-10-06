from copy import deepcopy
from decimal import Decimal as D

import pytest

from scripts.diagnose_donchian_setup2 import (
    BINS, analyze, band, join_trades, needs_review, number, ranked_winners, stats,
)


def fixture_trade(market='spot', at='2022-01-01T00:00:00+00:00', exit_price='109'):
    side = 1 if market == 'spot' else -1
    gross = side*D(2)*(D(exit_price)-100)
    t = dict(opened_at=at, closed_at='2022-01-02T00:00:00+00:00', market=market,
        symbol='SOLUSDT', side='long' if side == 1 else 'short', entry_price='100',
        exit_price=exit_price, quantity='2', gross_pnl=str(gross), exchange_fee='1',
        slippage_cost='1', funding_paid='-0.5', net_pnl=str(gross-D('1.5')),
        exit_reason='atr_trailing_touch')
    e = dict(kind='entry_features', at=at, market=market, symbol='SOLUSDT',
        signal_available_at=at, stop=str(100-side*9), features=dict(adx='25', prior_adx='24',
        atr='3', close='100', ema50=str(100-side*3), volume='200', volume_ma='100'))
    return t, e


@pytest.mark.parametrize('feature', BINS)
def test_locked_boundaries(feature):
    low, mid, high = BINS[feature]
    assert band(feature, D(mid)) == f'({low},{mid}]'
    assert band(feature, D(high)) == f'({mid},{high}]'
    assert band(feature, D(high)+D('.000001')) == f'>{high}'
    assert band(feature, D(low)+D('.000001')) == f'({low},{mid}]'
    with pytest.raises(ValueError):
        band(feature, D(low))


@pytest.mark.parametrize('bad', [None, True, 'NaN', 'Infinity'])
def test_reject_bad_number(bad):
    with pytest.raises(ValueError):
        number(bad)


@pytest.mark.parametrize('market,exit_price', [('spot', '109'), ('perp', '91')])
def test_nominal_r_cost_credit_and_direction(market, exit_price):
    t, e = fixture_trade(market, exit_price=exit_price)
    row = join_trades([t], [e])[0]
    assert row['initial_risk_usdt'] == 18
    assert row['gross_r'] == 1
    assert row['cost_usdt'] == D('1.5')  # Negative funding is received.
    assert row['net_r'] == D('16.5')/18
    assert row['ema50_distance_atr'] == 1
    assert row['holding_hours'] == 24


@pytest.mark.parametrize('failure', ['duplicate_event', 'duplicate_trade', 'missing', 'orphan',
    'future', 'zero_atr', 'wrong_stop', 'pnl', 'direction', 'naive', 'outside'])
def test_bad_join_stops(failure):
    t, e = fixture_trade()
    trades, events = [t], [e]
    if failure == 'duplicate_event':
        events.append(deepcopy(e))
    elif failure == 'duplicate_trade':
        trades.append(deepcopy(t))
    elif failure == 'missing':
        events = []
    elif failure == 'orphan':
        trades = []
    elif failure == 'future':
        e['signal_available_at'] = '2022-01-01T04:00:00+00:00'
    elif failure == 'zero_atr':
        e['features']['atr'] = '0'
    elif failure == 'wrong_stop':
        e['stop'] = '101'
    elif failure == 'pnl':
        t['net_pnl'] = '1'
    elif failure == 'direction':
        t['side'] = 'short'
    elif failure == 'naive':
        t['opened_at'] = '2022-01-01T00:00:00'
    elif failure == 'outside':
        t['opened_at'] = e['at'] = '2021-12-31T00:00:00+00:00'
    with pytest.raises(ValueError):
        join_trades(trades, events)


def test_equivalent_timezone_keys():
    t, e = fixture_trade()
    e['at'] = '2022-01-01T07:00:00+07:00'
    assert len(join_trades([t], [e])) == 1


def test_entry_period_not_exit_period_and_empty_groups():
    t, e = fixture_trade(at='2023-12-31T20:00:00+00:00')
    t['closed_at'] = '2024-01-02T00:00:00+00:00'
    result = analyze(join_trades([t], [e]))
    g = result['groups'][0]
    assert len(result['groups']) == 112
    assert g['years']['2023']['count'] == 1
    assert g['years']['2024']['count'] == 0
    assert g['halves']['2023-H2']['net_pnl'] == D('16.5')
    assert not g['years']['2026']['complete_period']
    assert g['halves']['2026-H1']['complete_period']
    assert not g['halves']['2026-H2']['complete_period']
    assert stats([])['mean_net_r'] is None
    assert stats([])['profit_factor'] is None


def test_review_criterion_does_not_relax_or_use_partial_year():
    years = {str(y): {'count': 5, 'mean_net_r': D('-0.1')} for y in range(2022, 2027)}
    overall = {'count': 20, 'mean_net_r': D('-0.1')}
    assert needs_review(overall, years)['needs_review']
    years['2024']['count'] = 4
    years['2025']['mean_net_r'] = D(0)
    assert not needs_review(overall, years)['needs_review']  # 2026 cannot rescue.
    years['2024']['count'] = 5
    assert needs_review(overall, years)['needs_review']
    overall['count'] = 19
    assert not needs_review(overall, years)['needs_review']
    overall.update(count=20, mean_net_r=D(0))
    assert not needs_review(overall, years)['needs_review']


def test_usdt_and_r_rankings_and_overlap():
    t, e = fixture_trade()
    first = join_trades([t], [e])[0]
    second = deepcopy(first)
    second.update(trade_id='second', net_pnl=D(100), net_r=D('.1'))
    assert ranked_winners([first, second], 'net_pnl')[0] == second
    assert ranked_winners([first, second], 'net_r')[0] == first
    s = stats([first, second], {first['trade_id']})
    assert s['global_top10_count'] == 1
    assert s['global_top10_net_usdt'] == D('16.5')
