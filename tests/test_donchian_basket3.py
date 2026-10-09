from decimal import Decimal as D

import pytest

from intraday.replay_v2.donchian_adx_config import cases, config_type
from intraday.replay_v2.donchian_adx_setups import (BASKETS, BasketConfig, BasketStressConfig,
                                                    basket_specification)
from test_donchian_adx_setups import prepared_fixture


def test_ten_baskets_at_two_cost_levels_plus_the_setup_2_control():
    variants = cases(universe='baskets')
    names = [n for n, _ in variants]
    assert len(BASKETS) == 10 and len(variants) == 21
    assert names[:3] == ['Setup-2', 'BTC-ETH-NEAR', 'BTC-ETH-NEAR-cost2x'] and names[-1] == 'NEAR-SOL-ZEC-cost2x'
    assert variants[0][1] == cases(universe='setups')[1][1]
    assert config_type('baskets') is BasketConfig
    for name, cfg in variants[1:]:
        assert type(cfg) is (BasketStressConfig if name.endswith('-cost2x') else BasketConfig)
        assert cfg.weights == cfg.perp_weights and len(cfg.weights) == 3 and sum(cfg.weights.values()) == 1
        assert max(cfg.weights.values())-min(cfg.weights.values()) <= D('1e-27')
        assert cfg.adx_by_market == {m: dict.fromkeys(cfg.weights, 20) for m in ('spot', 'perp')}
        assert (cfg.entry_cap, cfg.perp_cap, cfg.include_perp, cfg.filter_level) == (D('.6'), D('.4'), True, 4)
        assert getattr(cfg, 'cost_multiplier', 1) == (2 if name.endswith('-cost2x') else 1)
        assert cfg == type(cfg).model_validate_json(cfg.model_dump_json())


@pytest.mark.parametrize('basket', [('ETH', 'BTC', 'SOL'), ('BTC', 'BTC', 'ETH'), ('BTC', 'ETH', 'HYPE')])
def test_basket_must_be_three_ordered_candidate_coins(basket):
    with pytest.raises(ValueError, match='three distinct coins'):
        basket_specification(basket)


@pytest.mark.parametrize('field,changed', [
    ('weights', {'NEARUSDT': D('.3'), 'SOLUSDT': D('.4'), 'ZECUSDT': D('.3')}),
    ('adx_by_market', {'spot': dict.fromkeys(('NEARUSDT', 'SOLUSDT', 'ZECUSDT'), 20), 'perp': {}}),
    ('basket', ('BTC', 'SOL', 'ZEC')), ('daily_loss', '.04'), ('capital', 650)])
def test_basket_rules_are_locked(field, changed):
    cfg = cases(universe='baskets')[-2][1]
    with pytest.raises(ValueError):
        BasketConfig.model_validate({**cfg.model_dump(), field: changed})


def test_basket_trades_only_its_coins_and_doubled_costs_cost_more():
    from intraday.replay_v2.donchian_filter_engine import simulate
    start, end, p = prepared_fixture()
    variants = dict(cases(start, end, universe='baskets'))
    base, stress = simulate(variants['NEAR-SOL-ZEC'], p), simulate(variants['NEAR-SOL-ZEC-cost2x'], p)
    coins = {'NEARUSDT', 'SOLUSDT', 'ZECUSDT'}
    assert {(t['market'], t['symbol']) for t in base['trades']} == {(m, s) for m in ('spot', 'perp') for s in coins}
    assert base['summary']['closed_trades'] == stress['summary']['closed_trades'] == 6
    assert stress['summary']['net_pnl'] < base['summary']['net_pnl']
    assert 'Spot20/10bps' in stress['methodology']['costs']


def test_basket_runner_and_ranking(tmp_path):
    from test_donchian_five import five_bundle
    from intraday.replay_v2.donchian_adx_study import run_study
    from intraday.replay_v2.donchian_basket_analysis import rank
    _, path = five_bundle(tmp_path)
    receipt = run_study(path, tmp_path/'runs', universe='baskets', progress=lambda _: None)
    assert receipt['preset'] == 'donchian-basket3' and len(receipt['results']) == 21
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    ranking = rank(tmp_path/'runs/comparison.json', tmp_path/'ranking')
    assert len(ranking['rows']) == 10 and ranking['control']['basket'].startswith('Setup-2')
    ratios = [r['return_to_drawdown'] for r in ranking['rows'] if r['return_to_drawdown'] is not None]
    assert ratios == sorted(ratios, reverse=True)
    assert all(r['return_cost2x_pct'] is not None for r in ranking['rows'])
    assert (tmp_path/'ranking/ranking.md').read_text().startswith('# Ten three-coin baskets')
