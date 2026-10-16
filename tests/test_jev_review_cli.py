import json
import sys
from pathlib import Path
from datetime import timedelta

import pytest

from intraday.__main__ import main
from intraday.jev_review import ReviewSignal
from intraday.jev_review_cache import AnswerCache
from intraday.setup2_review import encode_state
from test_setup2_review import observation, history, NOW, payload
from test_jev_review_overlay import route


def install_scan(monkeypatch):
    import intraday.jev_review_cli as cli
    signal = ReviewSignal('r', NOW, tuple(encode_state(observation(), history())))
    monkeypatch.setattr(cli, 'database_routes', lambda *a, **kw: [('config', 'data')])
    monkeypatch.setattr(cli, 'scan_route', lambda *a: (signal,))
    monkeypatch.setattr(cli, 'route_identity', lambda *a: {'symbol': 'ETHUSDT', 'market': 'spot', 'entry_bars': 1})
    return cli, signal


def argv(monkeypatch, tmp_path, *extra):
    monkeypatch.setattr(sys, 'argv', ['aigt', 'jev-review', 'collect', '--database', str(tmp_path/'source.sqlite3'),
        '--funding-dir', str(tmp_path), '--end', NOW.isoformat(), '--cache', str(tmp_path/'absent'/'cache.sqlite3'), *extra])


def test_dry_run_does_not_open_credentials_network_or_create_cache(tmp_path, monkeypatch, capsys):
    cli, _ = install_scan(monkeypatch)
    monkeypatch.setattr(cli.ProviderSecretStore, 'get', lambda *a: pytest.fail('must not read secrets'))
    monkeypatch.setattr(cli, 'ReviewProvider', lambda *a, **kw: pytest.fail('must not construct provider'))
    argv(monkeypatch, tmp_path, '--dry-run')
    main()
    result = json.loads(capsys.readouterr().out)
    assert result['missing_states'] == 1 and result['network_calls'] == 0
    assert not (tmp_path/'absent').exists()


def test_execute_requires_matching_plan_before_reading_credentials(tmp_path, monkeypatch):
    cli, _ = install_scan(monkeypatch)
    monkeypatch.setattr(cli.ProviderSecretStore, 'get', lambda *a: pytest.fail('must not read secrets'))
    argv(monkeypatch, tmp_path, '--execute', '--expected-plan-hash', 'a'*64)
    with pytest.raises(SystemExit, match='plan'):
        main()
    assert not (tmp_path/'absent').exists()


def test_fake_execute_populates_only_dedicated_cache_and_resumes(tmp_path, monkeypatch, capsys):
    cli, signal = install_scan(monkeypatch)
    receipt = tmp_path/'approved-plan.json'
    argv(monkeypatch, tmp_path, '--dry-run', '--output', str(receipt))
    main()
    plan = json.loads(capsys.readouterr().out)
    from intraday.jev_review_provider import ReviewCall
    class Fake:
        def request(self, state):
            assert tuple(state) == signal.tokens
            return ReviewCall('success', payload())
    monkeypatch.setattr(cli.ProviderSecretStore, 'get', lambda *a: 'fake-credential')
    monkeypatch.setattr(cli, 'ReviewProvider', lambda *a, **kw: Fake())
    argv(monkeypatch, tmp_path, '--execute', '--expected-plan-hash', plan['plan_hash'],
         '--approved-plan', str(receipt),
         '--secrets-file', str(tmp_path/'fake-only'), '--profile-id', 'fake-profile')
    main()
    output = json.loads(capsys.readouterr().out)
    assert output['collection']['successful'] == 1
    with AnswerCache(tmp_path/'absent'/'cache.sqlite3') as cache:
        assert cache.get(signal.tokens) is not None
    # The same approved request set remains valid after a successful/partial run.
    argv(monkeypatch, tmp_path, '--execute', '--expected-plan-hash', plan['plan_hash'],
         '--approved-plan', str(receipt),
         '--secrets-file', str(tmp_path/'fake-only'), '--profile-id', 'fake-profile')
    main()
    resumed = json.loads(capsys.readouterr().out)
    assert resumed['collection']['network_calls'] == 0
    argv(monkeypatch, tmp_path, '--dry-run')
    main()
    assert json.loads(capsys.readouterr().out)['missing_states'] == 0
    assert not (tmp_path/'source.sqlite3').exists()


def test_cli_replay_publishes_both_control_and_veto_reports(route, tmp_path, monkeypatch, capsys):
    import intraday.jev_review_cli as cli
    from intraday.jev_review import scan_route
    config, data, baseline = route
    signals = scan_route(config, data)
    p = payload()
    p['answers']['action'] = dict(choice='skip', probabilities=dict(enter_full=.05, reduce=.05, skip=.9))
    cache_path = tmp_path/'cache.sqlite3'
    with AnswerCache(cache_path, writable=True) as cache:
        for state in {s.tokens for s in signals}: cache.put(state, p)
    monkeypatch.setattr(cli, 'database_routes', lambda *a, **kw: [(config, data)])
    monkeypatch.setattr(cli.ProviderSecretStore, 'get', lambda *a: pytest.fail('replay must not read credentials'))
    monkeypatch.setattr(sys, 'argv', ['aigt', 'jev-review', 'replay', '--database', str(tmp_path/'unused'),
        '--funding-dir', str(tmp_path), '--end', config.end.isoformat(), '--cache', str(cache_path),
        '--report-dir', str(tmp_path/'reports')])
    main()
    result = json.loads(capsys.readouterr().out)['results'][0]
    assert result['result_ids']['rule_only'] == baseline['result_id']
    assert result['summaries']['reviewed']['closed_trades'] == 0
    assert result['summaries']['reviewed']['blocked_entries']['jev_review_veto'] > 0
    assert result['counterfactual']
    for report in result['reports'].values():
        assert (Path(report['report_directory'])/'manifest.json').is_file()
