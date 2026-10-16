import json
import sys
from datetime import timedelta

import pytest

from intraday.__main__ import main
from intraday.jev_review import ReviewSignal
from intraday.jev_review_cache import AnswerCache
from intraday.setup2_review import encode_state
from test_setup2_review import observation, history, NOW, payload


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
    argv(monkeypatch, tmp_path, '--dry-run')
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
         '--secrets-file', str(tmp_path/'fake-only'), '--profile-id', 'fake-profile')
    main()
    output = json.loads(capsys.readouterr().out)
    assert output['collection']['successful'] == 1
    with AnswerCache(tmp_path/'absent'/'cache.sqlite3') as cache:
        assert cache.get(signal.tokens) is not None
    argv(monkeypatch, tmp_path, '--dry-run')
    main()
    assert json.loads(capsys.readouterr().out)['missing_states'] == 0
    assert not (tmp_path/'source.sqlite3').exists()
