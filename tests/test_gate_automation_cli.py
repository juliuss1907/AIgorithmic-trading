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
