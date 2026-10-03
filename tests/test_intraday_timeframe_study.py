from datetime import timedelta

import pytest

from test_historical_mixed_research import START, fixture_inputs
from test_intraday_timeframe_data import native_bundle


def test_matrix_has_unchanged_control_and_two_new_intraday_cases():
    from intraday.replay_v2.intraday_study import study_configs
    variants = study_configs(START, START+timedelta(hours=8))
    assert [name for name,_ in variants] == ['A-H4-D1-trailH1','B-H1-H4H8-trailH1','C-H1-H4H8-trailM15']
    assert variants[0][1].perp_trailing_interval == '1h'
    assert 'perp_signal_interval' not in variants[0][1].model_dump()
    assert variants[1][1].model_dump(exclude={'perp_trailing_interval'}) == variants[2][1].model_dump(exclude={'perp_trailing_interval'})


def test_three_intraday_reports_full_offline_reproduction(tmp_path):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    from intraday.replay_v2.intraday_study import run_study, study_configs
    from intraday.replay_v2.historical_mixed import simulate_historical
    from intraday.replay_v2.historical_data import fetch_candle_snapshot
    from intraday.replay_v2.intraday_data import decode_inputs
    from intraday.replay_v2.artifacts import publish_report
    cfg = study_configs(START, START+timedelta(hours=8), False)[0][1]
    spot, daily, perp, funding = fixture_inputs(cfg)
    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'backup')
    raw = native_bundle(cfg, tmp_path)
    native = decode_inputs(cfg, raw, perp)
    fine = {s:tuple(b for b in p['trade1h'] if b.opened_at >= START) for s,p in native.items()}
    reference = publish_report(tmp_path/'control', simulate_historical(cfg,spot,daily,perp,funding,trailing_bars=fine))
    receipt = run_study(source['backup'], tmp_path/'reports', start=cfg.start, end=cfg.end,
        perp_inputs=(perp,funding), intraday_inputs=raw, loader=lambda store,c:(spot,daily),
        trend_filter=False, baseline_reference=reference['report_directory'])
    assert len(receipt['results']) == 3 and receipt['source_unchanged']
    assert receipt['baseline_reference_verified']
    assert all(r['deterministic_rerun_verified'] and r['common_grid_audit']['passive'] for r in receipt['results'])
    assert len({r['base_dataset_checksum'] for r in receipt['results']}) == 1
    assert len({r['result_id'] for r in receipt['results']}) == 3
    assert all((tmp_path/'reports'/r['audit_file']).is_file() for r in receipt['results'])
    assert 'UTC+7' in (tmp_path/'reports'/'comparison.md').read_text()


def test_cli_intraday_preset_requires_explicit_frozen_base_and_native_inputs(monkeypatch):
    from intraday.replay_v2 import mixed_portfolio_research as cli, intraday_study
    calls = []
    monkeypatch.setattr(intraday_study,'run_study',lambda *a,**kw:calls.append(kw) or {'results':[None]*3,'source_unchanged':True})
    base = ['--database','x','--report-root','y','--mode','historical-quant','--preset','perp-intraday-timeframes']
    cli.main(base+['--perp-inputs','p','--intraday-inputs','i'])
    assert calls[0]['inputs_path'] == 'p' and calls[0]['intraday_inputs_path'] == 'i'
    for flags in [[], ['--perp-inputs','p'], ['--collect-perp','--collect-intraday'],
                  ['--perp-inputs','p','--collect-intraday','--intraday-inputs','i'],
                  ['--perp-inputs','p','--intraday-inputs','i','--drawdown-policy','terminal'],
                  ['--perp-inputs','p','--intraday-inputs','i','--collect-trailing']]:
        with pytest.raises(SystemExit):
            cli.main(base+flags)
