from datetime import datetime, timezone
import json
import sys

from intraday.__main__ import main
from intraday.config import IntradayConfig
from intraday.replay_v2.research import run_weekly_confidence


NOW = datetime(2026, 10, 5, 2, tzinfo=timezone.utc)


def test_status_is_read_only_and_does_not_need_source_or_exchange_keys(tmp_path, monkeypatch, capsys):
    root = tmp_path/"missing-reports"
    monkeypatch.setattr(sys, "argv", ["aigt","replay","confidence","status","ETH","--report-dir",str(root)])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["proposal"] is None
    assert not root.exists()


def test_study_dispatches_isolated_research_with_explicit_source(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("intraday.replay_v2.research.run_research", lambda *a,**k:calls.append((a,k)) or {"study_id":"test"})
    monkeypatch.setenv("INTRADAY_PROVIDER_SECRETS_FILE", str(tmp_path/"configured-provider.toml"))
    monkeypatch.setattr(sys, "argv", ["aigt","replay","study","--coins","ETH","NEAR","ZEC","SOL",
        "--database",str(tmp_path/"source.sqlite"),"--report-dir",str(tmp_path/"reports"),"--offline"])
    main()
    assert json.loads(capsys.readouterr().out)["study_id"] == "test"
    assert calls[0][0][1] == ["ETH","NEAR","ZEC","SOL"]
    assert calls[0][1]["collect"] is False
    assert calls[0][1]["secrets_file"] == tmp_path/"configured-provider.toml"


def test_scheduler_is_off_by_default_and_runs_only_selected_coins(tmp_path):
    base = IntradayConfig(database=tmp_path/"source.sqlite")
    assert run_weekly_confidence(base, now=NOW)["status"] == "disabled"
    enabled = IntradayConfig(database=base.database, confidence_review_enabled=True)
    calls = []
    run_weekly_confidence(enabled, now=NOW, root=tmp_path/"reports",
                          runner=lambda *a,**k:calls.append((a,k)) or {})
    assert calls[0][0][1] == ["ETHUSDT","NEARUSDT","ZECUSDT","SOLUSDT"]
    assert "BTCUSDT" not in calls[0][0][1] and "HYPEUSDT" not in calls[0][0][1]
