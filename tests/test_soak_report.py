import json
import stat
from datetime import datetime, timezone

import pytest

from intraday.soak_report import (
    build_soak_readiness_report,
    collect_database_metrics,
    write_report,
)
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)


def snapshot(*, status="SOAK_ACTIVE", preview="deferred", persisted=None):
    return {
        "generated_at": NOW.isoformat(),
        "status": {"code": status, "label": "Soak active", "reasons": []},
        "soak": {
            "evidence_version": "scope-price-v2",
            "started_at": "2026-09-25T13:00:00+00:00",
            "target_at": NOW.isoformat(),
            "elapsed_seconds": 259200.0,
            "remaining_seconds": 0.0,
            "progress_pct": 100.0,
            "preview": {
                "evaluation_id": "preview-evaluation-id",
                "evidence_version": "scope-price-v2",
                "status": preview,
                "started_at": "2026-09-25T13:00:00+00:00",
                "evaluated_at": NOW.isoformat(),
                "duration_hours": 72.0,
                "sample_counts": {"spot_daily": 3, "perp_intraday": 4000},
                "availability": {"spot_daily": 1.0, "perp_intraday": 0.999},
                "hard_risk_violations": 0,
                "reason_codes": [],
            },
            "persisted_evaluation": persisted,
        },
        "counts": {"signals": 4003, "fills": 2, "trades": 1},
        "scopes": {
            "spot_daily": {
                "samples": 3,
                "availability": 1.0,
                "latest_status": "skipped_no_setup",
                "latest_at": NOW.isoformat(),
                "heartbeat_age_seconds": 0.0,
                "healthy": True,
                "private_scope": "must-not-leak",
            },
            "perp_intraday": {
                "samples": 4000,
                "availability": 0.999,
                "latest_status": "success",
                "latest_at": NOW.isoformat(),
                "heartbeat_age_seconds": 0.0,
                "healthy": True,
            },
        },
        "providers": {
            "jev": {
                "active": True,
                "profile_id": "jev-primary",
                "kind": "openrouter_decisions",
                "model": "example/jev",
                "preflight_status": "ok",
                "latest_call": {"status": "success", "error_code": None},
            }
        },
        "daily_model_cost_usd": 0.42,
        "scheduler": [{"job_name": "portfolio_soak_perp", "status": "success"}],
        "portfolio": {
            "paper_active": False,
            "entries_paused": True,
            "halt_reason": "soak_not_promoted",
            "private_position": "must-not-leak",
        },
        "signals": {"recent": [{"state_snapshot": "must-not-leak"}]},
        "recent_model_calls": [{"request_hash": "must-not-leak"}],
        "thesis": {"prompt": "must-not-leak"},
        "markets": {"perp": {"reference_price": 100000}},
    }


def database_metrics(*, integrity="ok"):
    return {
        "integrity": integrity,
        "size_bytes": 4096,
        "wal_size_bytes": 128,
        "disk_free_bytes": 1_000_000,
        "disk_total_bytes": 2_000_000,
        "database_path": "must-not-leak",
    }


@pytest.mark.parametrize(
    ("status", "preview", "persisted", "integrity", "expected"),
    [
        ("PAPER_ACTIVE", "pass", None, "ok", "none"),
        ("DEGRADED", "deferred", None, "ok", "investigate"),
        ("SOAK_ACTIVE", "reject", None, "ok", "investigate"),
        ("SOAK_ACTIVE", "deferred", None, "failed", "investigate"),
        (
            "SOAK_ACTIVE",
            "deferred",
            {"evaluation_id": "persisted-id", "status": "pass"},
            "ok",
            "request_paper_activation",
        ),
        ("SOAK_ACTIVE", "pass", None, "ok", "run_evaluation"),
        ("SOAK_ACTIVE", "deferred", None, "ok", "wait"),
    ],
)
def test_report_recommends_one_non_mutating_next_action(
    status, preview, persisted, integrity, expected
):
    report = build_soak_readiness_report(
        snapshot(status=status, preview=preview, persisted=persisted),
        database_metrics(integrity=integrity),
    )

    assert report["recommended_action"] == expected


def test_report_is_bounded_and_excludes_sensitive_dashboard_fields():
    report = build_soak_readiness_report(snapshot(), database_metrics())

    assert report["report_schema_version"] == "1"
    assert report["safety"] == {
        "paper_active": False,
        "entries_paused": True,
        "halt_reason": "soak_not_promoted",
        "signal_count": 4003,
        "fill_count": 2,
        "trade_count": 1,
    }
    assert report["model_cost"] == {"daily_usd": 0.42}
    serialized = json.dumps(report)
    for secret in ("must-not-leak", "state_snapshot", "request_hash", "prompt"):
        assert secret not in serialized


def test_collect_database_metrics_checks_live_database_read_only(tmp_path):
    database = tmp_path / "intraday.sqlite3"
    store = IntradayStore(database)
    before = store.latest_portfolio_soak_evaluation()

    metrics = collect_database_metrics(database)

    assert metrics["integrity"] == "ok"
    assert metrics["size_bytes"] > 0
    assert metrics["wal_size_bytes"] >= 0
    assert metrics["disk_free_bytes"] > 0
    assert metrics["disk_total_bytes"] >= metrics["disk_free_bytes"]
    assert store.latest_portfolio_soak_evaluation() == before


def test_write_report_is_private_atomic_and_refuses_overwrite(tmp_path):
    destination = tmp_path / "reports" / "soak.json"
    payload = {"report_schema_version": "1", "recommended_action": "wait"}

    written = write_report(payload, destination)

    assert written == destination.resolve()
    assert json.loads(destination.read_text(encoding="utf-8")) == payload
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not list(destination.parent.glob(".soak-report.*"))
    with pytest.raises(FileExistsError, match="already exists"):
        write_report(payload, destination)
