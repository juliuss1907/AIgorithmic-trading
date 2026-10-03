import pytest

from intraday.replay_v2.historical_mixed import HistoricalConfig
from intraday.replay_v2.historical_study import run_study, study_configs
from test_historical_mixed_research import START, WIDTH, fixture_inputs


PRESET = 'perp-realized-trailing'


def test_study_is_six_unique_factorial_configs_with_same_sizing_controls():
    rows = study_configs(START, START+WIDTH, preset=PRESET)
    assert len(rows) == len({c.model_dump_json() for _, c in rows}) == 6
    assert {(c.capital_growth, c.perp_trade_exit, c.perp_daily_policy) for _, c in rows if c.include_perp} == {
        (growth, 'baseline', 'target5') for growth in ('capped', 'realized')} | {
        (growth, 'net-trailing-3pp', 'none') for growth in ('capped', 'realized')}
    assert {c.capital_growth for _, c in rows if not c.include_perp} == {'capped', 'realized'}
    assert all(c.perp_stop == 'atr14-3x' for _, c in rows if c.include_perp)


def test_cli_preset_is_historical_only(monkeypatch):
    from intraday.replay_v2 import historical_study, mixed_portfolio_research as cli
    calls = []
    monkeypatch.setattr(historical_study, 'run_study', lambda *a, **kw: calls.append(kw) or
                        {'results': [None]*6, 'source_unchanged': True})
    cli.main(['--mode', 'historical-quant', '--preset', PRESET, '--database', 'x',
              '--report-root', 'y', '--perp-inputs', 'z', '--drawdown-policy', 'observe-only'])
    assert calls[0]['preset'] == PRESET
    with pytest.raises(SystemExit):
        cli.main(['--preset', PRESET, '--database', 'x', '--report-root', 'y'])


def test_six_reports_reproduce_offline_and_pair_by_sizing_and_exit_policy(tmp_path):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'evidence')
    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    receipt = run_study(source['backup'], tmp_path/'reports', start=cfg.start, end=cfg.end,
        loader=lambda store, config: (spot, daily), perp_inputs=(perp, funding), trend_filter=False,
        preset=PRESET, drawdown_policy='observe-only')
    assert len(receipt['results']) == 6 and receipt['source_unchanged']
    assert all(row['deterministic_rerun_verified'] for row in receipt['results'])
    for row in receipt['results']:
        refs = [r for r in receipt['results'] if r['include_perp'] == row['include_perp']
                and r['capital_growth'] == 'capped' and r['perp_trade_exit'] == row['perp_trade_exit']]
        assert len(refs) == 1
        net, ref = row['summary']['net_return_pct'], refs[0]['summary']['net_return_pct']
        assert row['paired_capped_return_difference_pp'] == (net-ref if net is not None and ref is not None else None)
    text = (tmp_path/'reports'/'comparison.md').read_text()
    assert 'separate realized' in text and '3 percentage points' in text
    assert 'observe-only' in text and 'UTC+7' in text
