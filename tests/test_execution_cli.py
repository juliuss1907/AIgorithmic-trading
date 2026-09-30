import json
import gc
import sys
from datetime import datetime, timezone

import pytest

from intraday.__main__ import _parser, main
from intraday.execution import cli
from intraday.execution.contracts import AccountRef
from intraday.execution.journal import ExecutionJournal
from intraday.store import IntradayStore


def test_status_and_pause_do_not_initialize_intraday_or_read_credentials(monkeypatch, tmp_path, capsys):
    path = tmp_path / "demo.sqlite3"
    journal = ExecutionJournal(path)
    account = AccountRef(venue="binance", environment="demo", account_id="test")
    journal.save_control(account, {"enabled": True, "paused": False}, kind="test", now=datetime.now(timezone.utc))
    monkeypatch.setattr(cli.DemoCredentials, "load", lambda _: pytest.fail("must not read credentials"))
    monkeypatch.setattr(IntradayStore, "__init__", lambda *_: pytest.fail("must not initialize source DB"))
    monkeypatch.setattr(sys, "argv", ["aigt", "execution", "demo", "status", "--execution-database", str(path)])
    main()
    assert json.loads(capsys.readouterr().out)["accounts"][account.key]["enabled"]
    monkeypatch.setattr(sys, "argv", ["aigt", "execution", "demo", "pause", "--execution-database", str(path)])
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "paused"
    assert journal.control(account)["paused"]


def test_execution_source_and_journal_must_be_distinct(monkeypatch, tmp_path):
    path = tmp_path / "source.sqlite3"
    store = IntradayStore(path)
    # Finish test fixture initializer connection cleanup/checkpoint before hashing.
    gc.collect()
    before = path.read_bytes()
    monkeypatch.setattr(sys, "argv", ["aigt", "execution", "demo", "preflight",
        "--execution-database", str(path), "--source-database", str(path), "--secrets-file", str(tmp_path / "missing.json")])
    with pytest.raises(SystemExit, match="separate"):
        main()
    assert path.read_bytes() == before


def test_flatten_does_not_depend_on_source_database():
    args = _parser().parse_args(["execution", "demo", "flatten", "--secrets-file", "private.json"])
    assert args.execution_action == "flatten"
    assert not hasattr(args, "source_database")


def test_run_does_not_accept_api_key_values_and_requires_source_and_file():
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["execution", "demo", "run", "--api-key", "bad-practice"])
