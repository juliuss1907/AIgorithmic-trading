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


def test_scheduler_skips_this_week_and_pending_proposals(tmp_path):
    from datetime import timedelta
    from intraday.replay_v2.confidence_reviews import ReviewStore
    root = tmp_path/"reports"
    reviews = ReviewStore(root)
    reviews.claim("ETH",NOW-timedelta(days=7),{})
    reviews.finish("ETH",NOW-timedelta(days=7),{"status":"pending_review"})
    reviews.claim("NEAR",NOW,{})
    reviews.finish("NEAR",NOW,{"status":"error"})
    enabled = IntradayConfig(database=tmp_path/"source.sqlite",confidence_review_enabled=True)
    calls = []
    runner = lambda *a,**k:calls.append((a,k)) or {}
    run_weekly_confidence(enabled,now=NOW,root=root,runner=runner)
    assert calls[0][0][1] == ["ZECUSDT","SOLUSDT"]
    assert run_weekly_confidence(enabled,now=NOW+timedelta(days=1),root=root,runner=runner)["status"] == "not_due"
    assert len(calls) == 1


def test_new_review_suppresses_only_matching_generic_perp_proposals(tmp_path, monkeypatch):
    from intraday.__main__ import _run_asset_auto_proposal_tick
    from intraday.store import IntradayStore
    store = IntradayStore(tmp_path/"source.sqlite")
    calls = []
    monkeypatch.setattr("intraday.__main__.auto_propose_asset_rule",
                        lambda store,symbol,scope,**kw:calls.append((symbol,scope.value)))
    _run_asset_auto_proposal_tick(store,client=object(),now=NOW,
                                 confidence_review_symbols=("ETHUSDT",))
    assert ("ETHUSDT","perp_intraday") not in calls
    assert ("ETHUSDT","spot_4h") in calls
    assert ("BTCUSDT","perp_intraday") in calls


def test_compose_persists_weekly_reports_shared_with_read_only_web():
    from pathlib import Path
    import yaml
    services = yaml.safe_load(Path("deploy/intraday/compose.yaml").read_text())["services"]
    worker = services["worker"]
    assert worker["environment"]["INTRADAY_REPLAY_REPORT_DIR"] == "/app/state/replay-reports"
    assert "replay-reports:/app/state/replay-reports" in worker["volumes"]
    web_mount = next(v for v in services["web"]["volumes"]
                     if isinstance(v,dict) and v.get("source") == "replay-reports")
    assert web_mount["read_only"] is True
    assert "INTRADAY_CONFIDENCE_REVIEW_ENABLED" not in worker["environment"]
