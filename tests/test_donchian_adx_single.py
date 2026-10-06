from copy import deepcopy
from decimal import Decimal as D

import pytest

from intraday.replay_v2.donchian_adx_config import cases, config_type
from intraday.replay_v2.donchian_adx_single import SingleCoinADXConfig
from intraday.replay_v2.metrics import fingerprint


def test_three_isolated_accounts_and_roundtrip():
    variants = cases(universe='single')
    assert [c.coin for _, c in variants] == ['SOLUSDT', 'ZECUSDT', 'NEARUSDT']
    assert config_type('single') is SingleCoinADXConfig
    for _, cfg in variants:
        assert cfg.weights == cfg.perp_weights == {cfg.coin: D(1)}
        assert cfg.capital*cfg.entry_cap == 600 and cfg.capital*cfg.perp_cap == 400
        assert cfg.adx_by_market == {m: {cfg.coin: 20} for m in ('spot', 'perp')}
        assert cfg == SingleCoinADXConfig.model_validate_json(cfg.model_dump_json())


@pytest.mark.parametrize('update', [
    {'coin': 'BTCUSDT'}, {'capital': 400}, {'entry_cap': '.5'}, {'daily_loss': '.04'},
    {'adx_threshold': 25}, {'weights': {'SOLUSDT': '.4', 'ZECUSDT': '.6'}},
    {'perp_weights': {'NEARUSDT': '1'}}, {'include_perp': False},
    {'adx_by_market': {'spot': {'SOLUSDT': 20}, 'perp': {'SOLUSDT': 25}}},
    {'perp_daily_policy': 'none'}, {'perp_size': 'two-thirds'}, {'capital_growth': 'equity'},
])
def test_single_rules_cannot_be_changed(update):
    raw = cases(universe='single')[0][1].model_dump(mode='json')
    with pytest.raises(ValueError):
        SingleCoinADXConfig.model_validate({**raw, **update})


def test_only_selected_coin_can_trade_or_pay_funding():
    from test_donchian_adx_setups import prepared_fixture
    from intraday.replay_v2.donchian_filter_engine import simulate
    start, end, prepared = prepared_fixture()
    for _, cfg in cases(start, end, universe='single'):
        report = simulate(cfg, prepared)
        assert {(t['market'], t['symbol']) for t in report['trades']} == {
            (m, cfg.coin) for m in ('spot', 'perp')}
        assert all(e.get('symbol', cfg.coin) == cfg.coin for e in report['events'])
        assert set(report['summary']['contributions']) == {m+':'+cfg.coin for m in ('spot', 'perp')}
        assert fingerprint(report) == fingerprint(simulate(
            SingleCoinADXConfig.model_validate_json(cfg.model_dump_json()), prepared))


def test_single_runner_full_repeat_resume_and_missing_source(tmp_path):
    from test_donchian_five import five_bundle
    from intraday.replay_v2.donchian_adx_study import run_study
    from intraday.replay_v2.metrics import encoded
    _, path = five_bundle(tmp_path)
    root = tmp_path/'runs'
    receipt = run_study(path, root, universe='single', progress=lambda _: None)
    assert receipt['preset'] == 'donchian-adx-single'
    assert len(receipt['results']) == 3
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert len({r['dataset_checksum'] for r in receipt['results']}) == 1
    assert 'Three independent1000USDT accounts' in (root/'comparison.md').read_text()
    assert receipt == run_study(path, root, universe='single', resume=True, progress=lambda _: None)
    with pytest.raises(ValueError, match='binding changed'):
        run_study(path, root, universe='setups', resume=True, progress=lambda _: None)
    broken = deepcopy(receipt)
    broken['results'][0]['config_checksum'] = 'changed'
    (root/'case-0.json').write_text(encoded(broken['results'][0]))
    with pytest.raises(ValueError, match='checkpoint binding changed'):
        run_study(path, root, universe='single', resume=True, progress=lambda _: None)
    target = tmp_path/'NEARUSDT-spot15m.json'
    target.write_text(target.read_text()+' ')
    with pytest.raises(ValueError, match='checksum changed'):
        run_study(path, tmp_path/'missing', universe='single', progress=lambda _: None)


def test_setup_two_full_reference_verification(tmp_path):
    from test_donchian_five import five_bundle
    from intraday.replay_v2.donchian_adx_study import run_study, verify_three_coin
    _, path = five_bundle(tmp_path)
    original = run_study(path, tmp_path/'setups', universe='setups', progress=lambda _: None)
    checked = verify_three_coin(tmp_path/'setups/comparison.json', tmp_path/'verified',
                                universe='setups', progress=lambda _: None)
    assert len(checked) == 1 and checked[0]['matched']
    assert checked[0]['result_id'] == original['results'][1]['result_id']


def test_verified_analysis_reconciles_and_rejects_changed_case(tmp_path):
    from test_donchian_five import five_bundle
    from intraday.replay_v2.donchian_adx_study import run_study
    from intraday.replay_v2.donchian_single_analysis import describe
    from intraday.replay_v2.metrics import encoded
    _, path = five_bundle(tmp_path)
    single = run_study(path, tmp_path/'single', universe='single', progress=lambda _: None)
    old = run_study(path, tmp_path/'setups', universe='setups', progress=lambda _: None)
    result = describe(tmp_path/'single/comparison.json', tmp_path/'analysis',
                      baseline_path=tmp_path/'setups/comparison.json')
    assert result['independent_accounts'] and result['total_initial_capital'] == 3000
    assert len(result['results']) == 3
    assert [r['setup_two_initial_allocation'] for r in result['baseline']['comparisons']] == [400, 300, 300]
    assert all(len(r['coin_market']) == 2 for r in result['results'])
    assert abs(sum(r['setup_two_contribution']['net_pnl'] for r in result['baseline']['comparisons'])-
               old['results'][1]['summary']['net_pnl']) < 1e-7
    assert (tmp_path/'analysis/trade-samples.csv').read_text().startswith('case,symbol,market,side,')
    assert 'not an isolated account return' in (tmp_path/'analysis/report.md').read_text()
    single['results'][0]['config_checksum'] = 'changed'
    (tmp_path/'single/comparison.json').write_text(encoded(single))
    with pytest.raises(ValueError, match='identity changed'):
        describe(tmp_path/'single/comparison.json', tmp_path/'rejected',
                 baseline_path=tmp_path/'setups/comparison.json')
    assert not (tmp_path/'rejected').exists()


def test_hold_times_and_extreme_trades_are_after_costs():
    from intraday.replay_v2.donchian_single_analysis import detailed_outcomes
    trades = [dict(net_pnl=v, opened_at='2022-01-01T00:00:00+00:00',
                   closed_at=f'2022-01-01T0{h}:00:00+00:00') for v, h in [('3', 1), ('-2', 2), ('0', 3)]]
    stats = detailed_outcomes(trades)
    assert stats['wins'] == stats['losses'] == stats['breakeven'] == 1
    assert stats['holding_hours'] == dict(mean=2, median=2, maximum=3)
    assert stats['largest_win'] == trades[0] and stats['largest_loss'] == trades[1]
    assert detailed_outcomes([])['largest_win'] is None
    assert detailed_outcomes([trades[0]])['largest_loss'] is None
