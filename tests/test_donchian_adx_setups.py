from copy import deepcopy
from decimal import Decimal as D

import pytest

from intraday.replay_v2.donchian_adx_config import cases, START, END
from intraday.replay_v2.donchian_adx_setups import ADXSetupConfig, market_weights
from intraday.replay_v2.metrics import fingerprint


def test_five_requested_configs_roundtrip_and_locked_rules():
    variants = cases(universe='setups')
    assert [c.setup for _, c in variants] == [1, 2, 3, 4, 5]
    one, two, three, four, five = [c for _, c in variants]
    assert one.weights == dict(BTCUSDT=D('.26'), ETHUSDT=D('.195'), SOLUSDT=D('.195'),
                               NEARUSDT=D('.175'), ZECUSDT=D('.175'))
    assert one.adx_by_market['spot']['NEARUSDT'] == 25
    assert two.weights == three.weights == dict(SOLUSDT=D('.4'), NEARUSDT=D('.3'), ZECUSDT=D('.3'))
    assert three.entry_cap == 1 and three.perp_cap == 0 and not three.include_perp
    assert not market_weights(three, 'perp')
    assert four.weights == five.perp_weights == dict(BTCUSDT=D('.5'), ETHUSDT=D('.5'))
    assert sum(four.perp_weights.values()) == 1
    assert max(four.perp_weights.values())-min(four.perp_weights.values()) <= D('1e-28')
    for _, cfg in variants:
        assert cfg == ADXSetupConfig.model_validate_json(cfg.model_dump_json())
        for field, changed in [('capital', 650), ('daily_loss', '.04'), ('leverage', 2),
                               ('adx_by_market', {'spot': {'SOLUSDT': 18}}), ('weights', two.weights)]:
            if getattr(cfg, field) == changed:
                continue
            with pytest.raises(ValueError):
                ADXSetupConfig.model_validate({**cfg.model_dump(), field: changed})
    assert fingerprint(cases()[0][1].model_dump(mode='json')) == 'd601d9ba4f1c5f4f3de5583c89eb16b40d97f158d1125452a705048f7baa9205'


def prepared_fixture():
    from test_donchian_filter_engine import fixture
    from intraday.replay_v2.donchian_adx_config import FiveCoinADXConfig
    from intraday.replay_v2.donchian_filter_engine import prepare
    cfg, data, funding = fixture()
    for s in ('NEARUSDT', 'ZECUSDT'):
        data[s] = deepcopy(data['BTCUSDT'])
        funding[s] = funding['BTCUSDT'].model_copy(update={'symbol': s})
    p = prepare(FiveCoinADXConfig(start=cfg.start, end=cfg.end), data, funding)
    for market, groups in p.features.items():
        for rows in groups.values():
            f = rows[cfg.start]
            f.update(adx=D(22), prior_adx=D(21), ema200=D(100), volume_ma=D(100), volume=D(160),
                     profile={'val': 100, 'vah': 100}, ema50=D(105 if market == 'spot' else 95),
                     prior_ema50=D(104 if market == 'spot' else 96),
                     plus_di=D(60 if market == 'spot' else 10),
                     minus_di=D(10 if market == 'spot' else 60))
    return cfg.start, cfg.end, p


@pytest.mark.parametrize('setup', [1, 2, 3, 4, 5])
def test_market_coin_threshold_dispatch_spot_only_and_accounting(setup):
    from intraday.replay_v2.donchian_filter_engine import simulate
    start, end, p = prepared_fixture()
    cfg = cases(start, end, universe='setups')[setup-1][1]
    report = simulate(cfg, p)
    actual = {(t['market'], t['symbol']) for t in report['trades']}
    expected = {(m, s) for m, thresholds in cfg.adx_by_market.items()
                for s, threshold in thresholds.items() if threshold == 20}
    assert actual == expected
    assert all(t['side'] == ('long' if t['market'] == 'spot' else 'short') for t in report['trades'])
    assert report['summary']['closed_trades'] == len(expected)
    assert abs(sum(D(str(t['net_pnl'])) for t in report['trades'])-
               D(str(report['summary']['net_pnl']))) < D('1e-12')
    assert len(report['summary']['contributions']) == sum(len(market_weights(cfg, m)) for m in ('spot', 'perp'))
    assert fingerprint(report) == fingerprint(simulate(ADXSetupConfig.model_validate_json(cfg.model_dump_json()), p))
    if setup == 3:
        assert report['config']['market'] == 'spot'
        assert report['summary']['funding_paid_known'] == 0
        assert not [e for e in report['events'] if e.get('market') == 'perp']
        assert all(D(row['perp_gross_notional']) == D(row['locked_margin']) == 0 for row in report['equity_curve'])
    rejected = {(e['market'], e['symbol']) for e in report['events'] if e['reason'] == 'indicator_filters'}
    assert {(m, s) for m, thresholds in cfg.adx_by_market.items()
            for s, threshold in thresholds.items() if threshold == 25} <= rejected


def test_setups_runner_manifest_full_repeat_resume_and_binding(tmp_path):
    from test_donchian_five import five_bundle
    from intraday.replay_v2.donchian_adx_study import run_study
    _, path = five_bundle(tmp_path)
    receipt = run_study(path, tmp_path/'runs', universe='setups', progress=lambda _: None)
    assert receipt['preset'] == 'donchian-adx-setups'
    assert len(receipt['results']) == 5
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert len({r['dataset_checksum'] for r in receipt['results']}) == 1
    from intraday.replay_v2.donchian_setups_analysis import describe
    analysis = describe(tmp_path/'runs/comparison.json', tmp_path/'analysis')
    assert len(analysis['results']) == 5
    assert len(analysis['results'][2]['coin_market']) == 3
    assert not any(k.startswith('perp/') for k in analysis['results'][2]['coin_market'])
    assert (tmp_path/'analysis/trade-samples.csv').read_text().startswith('setup,symbol,market,side,')
    assert receipt == run_study(path, tmp_path/'runs', universe='setups', resume=True, progress=lambda _: None)
    with pytest.raises(ValueError, match='binding changed'):
        run_study(path, tmp_path/'runs', universe='five', resume=True, progress=lambda _: None)


def test_prepared_wrong_universe_fails_before_simulating():
    from test_donchian_filter_engine import fixture
    from intraday.replay_v2.donchian_filter_engine import prepare, simulate
    cfg, data, funding = fixture()
    p = prepare(cfg, data, funding)
    with pytest.raises(ValueError, match='missing configured'):
        simulate(cases(cfg.start, cfg.end, universe='setups')[1][1], p)


def test_outcomes_use_net_after_costs_and_report_breakeven():
    from intraday.replay_v2.donchian_setups_analysis import outcomes
    stats = outcomes([{'net_pnl': v} for v in ['3', '-2', '0']])
    assert stats['wins'] == stats['losses'] == stats['breakeven'] == 1
    assert stats['winning_net_sum'] == 3 and stats['losing_net_sum'] == -2
    assert stats['profit_factor'] == 1.5 and stats['net_pnl'] == 1
