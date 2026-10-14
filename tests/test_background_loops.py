import json
import sqlite3

import intraday.__main__ as main


def test_supervised_loop_restarts_after_a_locked_database(monkeypatch, capsys):
    calls = []
    sleeps = []

    def flaky_loop(config):
        calls.append(config)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    main._supervised_loop(flaky_loop, "config", restart_delay=5)

    assert calls == ["config", "config"]
    assert sleeps == [5]
    record = json.loads(capsys.readouterr().out.strip())
    assert record == {"background_loop": {
        "loop": "flaky_loop", "status": "restarting", "code": "OperationalError",
    }}


def test_worker_starts_every_background_loop_supervised():
    source = open(main.__file__, encoding="utf-8").read()
    assert source.count("threading.Thread(") == 1
    assert "_start_loop(_asset_rule_bootstrap_loop, config)" in source
