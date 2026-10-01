from datetime import datetime, timedelta, timezone
import json
import sys

import pytest

from intraday.__main__ import main
from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
from intraday.store import IntradayStore


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def invoke(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["aigt", *arguments])
    main()


def test_cli_offline_dynamic_coin_run_list_show_and_saved_reports(tmp_path, monkeypatch, capsys):
    source = tmp_path/"source.sqlite"
    store = IntradayStore(source)
    store.register_asset("DOGE", market="spot", now=NOW)
    candidate = ScopedRuleCandidate.create(rule_id="r", parent_rule_id="bootstrap", thesis_id="offline",
        scope=DecisionScope.SPOT_4H, symbol="DOGEUSDT", parameters=SpotRuleParameters(),
        created_at=NOW, model_ref="operator", prompt_version="test")
    store.register_scoped_rule(candidate)
    rows = []
    for i in range(24):
        opening = int((NOW+timedelta(hours=4*i)).timestamp()*1000)
        rows.append([opening, 100, 101, 99, 100, 1, opening+14_400_000-1])
    store.record_asset_candles("DOGEUSDT", "4h", rows)
    root = tmp_path/"reports"
    args = ["--database",str(source), "--report-dir",str(root)]
    invoke(monkeypatch, ["replay", "run", "DOGE", "--market", "spot", "--rule", "r",
        "--from",(NOW+timedelta(hours=88)).isoformat(), "--to",(NOW+timedelta(hours=96)).isoformat(), *args])
    result = json.loads(capsys.readouterr().out)
    assert result["activation_allowed"] is False
    assert result["summary"]["initial_capital"] == 1000
    invoke(monkeypatch, ["replay", "list", "--report-dir",str(root)])
    assert json.loads(capsys.readouterr().out)["total"] == 1
    invoke(monkeypatch, ["replay", "show", result["run_id"], "--report-dir",str(root)])
    shown = json.loads(capsys.readouterr().out)
    assert shown["inputs"]["rule_id"] == "r"
    assert shown["research_only"]
    assert store.scoped_rule_status("r") == "queued"


def test_missing_source_never_creates_database(tmp_path, monkeypatch):
    path = tmp_path/"missing.sqlite"
    with pytest.raises(SystemExit):
        invoke(monkeypatch, ["replay", "run", "BTC", "--market","perp", "--rule","r",
            "--from",NOW.isoformat(), "--to",(NOW+timedelta(days=1)).isoformat(),
            "--database",str(path), "--report-dir",str(tmp_path/"reports")])
    assert not path.exists()


def test_configuration_validation_does_not_echo_unknown_secret_values(tmp_path, monkeypatch):
    path = tmp_path/"config.json"
    path.write_text(json.dumps({"secret": "DO-NOT-ECHO-THIS"}))
    with pytest.raises(SystemExit) as caught:
        invoke(monkeypatch, ["replay", "run", "BTC", "--market","perp", "--rule","r",
            "--from",NOW.isoformat(), "--to",(NOW+timedelta(days=1)).isoformat(),
            "--database",str(tmp_path/"source.sqlite"), "--config",str(path)])
    assert "DO-NOT-ECHO-THIS" not in str(caught.value)


def test_registered_deployment_routes_replay_without_touching_native_source(monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr("intraday.__main__.deployment_cli.load_deployment", lambda:SimpleNamespace(project_root="/project"))
    monkeypatch.setattr("intraday.__main__.deployment_cli.admin_command", lambda deployment,args:["docker-admin", *args])
    monkeypatch.setattr("intraday.__main__.deployment_cli.execute", lambda command, **kw:calls.append(command) or 0)
    invoke(monkeypatch, ["replay","list"])
    assert calls == [["docker-admin","replay","list"]]
