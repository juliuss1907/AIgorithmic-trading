import json
import sys

import pytest

from intraday.__main__ import main, _parser
from intraday.replay_v2.automation import policy
from test_gate_automation import perp_setup
from test_replay_v2_data import dump
from test_replay_gate_lifecycle import setup


def test_cli_policy_set_pause_status_and_future_scope(tmp_path,monkeypatch,capsys):
    store,rule,root = setup(tmp_path)
    for action in ('set','status','pause'):
        before = dump(store.database)
        monkeypatch.setattr(sys,'argv',['aigt','assets','rules','automation',action,'--market','all','--database',str(store.database)])
        main()
        result = json.loads(capsys.readouterr().out)
        assert result['policy']['perp']['interval_days'] == 7
        if action == 'status':
            assert dump(store.database) == before
    assert policy(store,'spot')['enabled'] == 0


def test_cli_migrate_dry_run_has_no_writes_and_partial_error_nonzero(tmp_path,monkeypatch,capsys):
    store,rule,later = perp_setup(tmp_path)
    from datetime import datetime
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return later
    monkeypatch.setattr('intraday.replay_v2.automation_cli.datetime',Clock)
    before = dump(store.database)
    monkeypatch.setattr(sys,'argv',['aigt','assets','rules','migrate-perp','DOGE','--dry-run','--database',str(store.database)])
    main()
    assert json.loads(capsys.readouterr().out)['dry_run'] is True
    assert dump(store.database) == before
    monkeypatch.setattr(sys,'argv',['aigt','assets','rules','migrate-perp','DOGE','MISSING','--database',str(store.database)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'partial_failure'
    assert [r['status'] for r in result['rows']] == ['selected','error']


def test_replay_engine_unselected_default_legacy_but_explicit_still_parse():
    assert _parser().parse_args(['assets','rules','replay','r']).engine is None
    assert _parser().parse_args(['assets','rules','replay','r','--engine','v1']).engine == 'v1'


def test_status_isolates_a_broken_route_and_remains_read_only(tmp_path,monkeypatch,capsys):
    import intraday.replay_v2.automation_cli as cli
    store,rule,root = setup(tmp_path)
    from intraday.replay_v2.automation import set_policy
    from test_spot_4h_lifecycle import NOW
    set_policy(store,'all',enabled=True,now=NOW)
    original = cli.projection
    def broken(reader,symbol,scope,**kwargs):
        if symbol == 'ETHUSDT':
            raise ValueError('binding invalid')
        return original(reader,symbol,scope,**kwargs)
    monkeypatch.setattr(cli,'projection',broken)
    before = dump(store.database)
    monkeypatch.setattr(sys,'argv',['aigt','assets','rules','automation','status','--database',str(store.database)])
    main()
    rows = json.loads(capsys.readouterr().out)['rows']
    assert rows and all(r['status']=='halted' for r in rows if r['symbol']=='ETHUSDT')
    assert any(r['symbol']!='ETHUSDT' for r in rows)
    assert dump(store.database) == before
