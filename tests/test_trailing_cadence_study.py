import pytest

from test_historical_mixed_research import START, WIDTH, fixture_inputs


def test_cadence_matrix_changes_only_trailing_interval():
    from intraday.replay_v2.trailing_cadence_study import study_configs
    configs = study_configs(START, START+WIDTH, False)
    assert [c.perp_trailing_interval for _, c in configs] == ['4h', '1h', '30m']
    assert len({c.model_dump_json(exclude={'perp_trailing_interval'}) for _, c in configs}) == 1
    assert all(c.capital_growth == 'realized' and c.drawdown_policy == 'observe-only' for _, c in configs)


def test_three_reports_offline_reproduce_with_separate_base_identity(tmp_path):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    from intraday.replay_v2.trailing_cadence_study import run_study, study_configs
    from intraday.replay_v2.trailing_data import collect_inputs
    from intraday.replay_v2.historical_data import fetch_candle_snapshot
    from datetime import timedelta
    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'evidence')
    cfg = study_configs(START, START+WIDTH, False)[0][1]
    spot, daily, perp, funding = fixture_inputs(cfg)
    def fetch(symbol, interval, start, end, **kw):
        width = 3600000 if interval == '1h' else 1800000
        opening = int(start.timestamp()*1000)
        rows = [[i, '100', '101', '99', '100', '1', i+width-1]
                for i in range(opening, int(end.timestamp()*1000), width)]
        return fetch_candle_snapshot(symbol, interval, start, end, fetch_json=lambda p,q: rows,
                                     now=end+timedelta(days=1))
    raw = collect_inputs(cfg, tmp_path, candle_fetcher=fetch)
    receipt = run_study(source['backup'], tmp_path/'result', start=START, end=START+WIDTH,
        loader=lambda store,c: (spot,daily), perp_inputs=(perp,funding), trailing_inputs=raw, trend_filter=False)
    assert len(receipt['results']) == 3 and receipt['source_unchanged']
    assert len({r['base_dataset_checksum'] for r in receipt['results']}) == 1
    assert len({r['result_id'] for r in receipt['results']}) == 3
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert 'UTC+7' in (tmp_path/'result'/'comparison.md').read_text()


def test_cli_requires_explicit_trailing_inputs_and_keeps_other_modes_unchanged(monkeypatch):
    from intraday.replay_v2 import mixed_portfolio_research as cli, trailing_cadence_study
    calls = []
    monkeypatch.setattr(trailing_cadence_study, 'run_study', lambda *a,**kw: calls.append(kw) or
        {'results': [None]*3, 'source_unchanged': True})
    base = ['--database','x','--report-root','y']
    cli.main(base+['--mode','historical-quant','--preset','perp-trailing-cadence',
                   '--perp-inputs','p','--collect-trailing'])
    assert calls[0]['collect_trailing'] and calls[0]['inputs_path'] == 'p'
    for flags in [
        ['--collect-trailing'],
        ['--mode','historical-quant','--perp-inputs','p','--collect-trailing'],
        ['--mode','historical-quant','--preset','perp-trailing-cadence','--perp-inputs','p'],
        ['--mode','historical-quant','--preset','perp-trailing-cadence','--perp-inputs','p',
         '--collect-trailing','--trailing-inputs','t'],
        ['--mode','historical-quant','--preset','perp-trailing-cadence','--perp-inputs','p',
         '--collect-trailing','--drawdown-policy','terminal'],
    ]:
        with pytest.raises(SystemExit):
            cli.main(base+flags)
