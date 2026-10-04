from datetime import timedelta
from decimal import Decimal as D

import pytest

from test_historical_mixed_research import START, fixture_inputs
from test_intraday_timeframe_data import native_bundle


def test_flow_aware_common_audit_never_treats_reserve_draw_as_profit(tmp_path):
    from test_short_reserve_engine import inputs
    from intraday.replay_v2.short_reserve_engine import simulate_short_reserve
    from intraday.replay_v2.intraday_audit import audit_drawdown
    args = inputs(tmp_path, 'restore-and-repay')
    rows = list(args[-1]['BTCUSDT']['mark15m'])
    rows[1] = rows[1].model_copy(update={'open':D(110), 'high':D(110)})
    args[-1]['BTCUSDT']['mark15m'] = tuple(rows)
    report = simulate_short_reserve(*args)
    audit, curve = audit_drawdown(report, args[1], args[-1])
    assert audit['perp_performance_flow_adjusted']
    assert audit['final_perp_equity'] == 300
    assert audit['final_perp_performance_equity'] < 300
    # Post-event audit sees the actual contract100 flatten, not the pre-fill
    # mark110 risk trigger. Its residual cost loss must not disappear on draw.
    assert audit['perp_max_drawdown_pct'] > 0
    assert D(curve[-1]['perp_performance_equity']) == D(curve[-1]['perp_equity'])-D(curve[-1]['perp_capital_flows'])


def test_four_case_matrix_publication_and_exact_old_reference(tmp_path):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    from intraday.replay_v2.short_reserve_study import run_study, study_configs
    from intraday.replay_v2.intraday_data import decode_inputs
    from intraday.replay_v2.intraday_study import replay
    from intraday.replay_v2.artifacts import publish_report
    configs = study_configs(START, START+timedelta(hours=8), False)
    assert len(configs) == 4
    cfg = configs[1][1]
    spot, daily, perp, funding = fixture_inputs(cfg, side=-1)
    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'backup')
    raw = native_bundle(cfg, tmp_path)
    native = decode_inputs(cfg, raw, perp)
    reference = publish_report(tmp_path/'reference', replay(cfg, spot, daily, perp, funding, native))
    receipt = run_study(source['backup'], tmp_path/'results', start=cfg.start, end=cfg.end,
        perp_inputs=(perp,funding), intraday_inputs=raw, loader=lambda store,c:(spot,daily),
        trend_filter=False, baseline_reference=reference['report_directory'])
    assert receipt['source_unchanged'] and receipt['baseline_reference_verified']
    assert len({r['result_id'] for r in receipt['results']}) == 4
    assert len({r['base_dataset_checksum'] for r in receipt['results']}) == 1
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert all(r['common_grid_audit']['passive'] for r in receipt['results'])
    assert 'UTC+7' in (tmp_path/'results'/'comparison.md').read_text()
    with pytest.raises(FileExistsError):
        run_study(source['backup'], tmp_path/'results', start=cfg.start, end=cfg.end,
            perp_inputs=(perp,funding), intraday_inputs=raw, loader=lambda store,c:(spot,daily), trend_filter=False)


def test_short_reserve_cli_requires_frozen_only_inputs(monkeypatch):
    from intraday.replay_v2 import mixed_portfolio_research as cli, short_reserve_study
    calls = []
    monkeypatch.setattr(short_reserve_study, 'run_study', lambda *a,**kw:
        calls.append(kw) or {'results':[None]*4, 'source_unchanged':True})
    base = ['--database','x','--report-root','y','--mode','historical-quant','--preset','perp-short-reserve']
    cli.main(base+['--perp-inputs','p','--intraday-inputs','i'])
    assert calls[0]['inputs_path'] == 'p' and calls[0]['intraday_inputs_path'] == 'i'
    for flags in [[], ['--perp-inputs','p'], ['--collect-perp','--intraday-inputs','i'],
            ['--perp-inputs','p','--collect-intraday'],
            ['--perp-inputs','p','--intraday-inputs','i','--reuse-perp-inputs','r'],
            ['--perp-inputs','p','--intraday-inputs','i','--drawdown-policy','terminal']]:
        with pytest.raises(SystemExit):
            cli.main(base+flags)
