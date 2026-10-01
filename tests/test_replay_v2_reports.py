from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from intraday.replay_v2.artifacts import publish_report, read_report, list_reports, read_series, resolve_report_dir


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def report():
    return {"schema_version":"2", "evaluator_version":"replay-v2.1", "result_id":"b"*64,
        "status":"limited", "research_only":True, "activation_allowed":False,
        "config":{"symbol":"ETHUSDT", "market":"spot", "start":NOW.isoformat(), "end":(NOW+timedelta(days=1)).isoformat()},
        "inputs":{}, "summary":{"net_pnl":None, "pnl_after_known_costs":-1, "closed_trades":1,
                                 "funding_complete":False, "max_drawdown_known_pct":.1},
        "limitations":["funding_coverage_incomplete"], "methodology":{}, "v1_reference":{},
        "equity_curve":[{"at":NOW.isoformat(), "equity_known":"1000"}],
        "trades":[{"quantity":"1"}], "events":[{"kind":"entry"},{"kind":"close"}]}


def test_atomic_reports_are_private_paginated_and_never_overwritten(tmp_path):
    root = tmp_path/"reports"
    first = publish_report(root, report(), now=NOW)
    second = publish_report(root, report(), now=NOW+timedelta(seconds=1))
    assert first["run_id"] != second["run_id"]
    assert len(first["run_id"]) == 32
    assert read_report(root, first["run_id"])["summary"]["net_pnl"] is None
    assert list_reports(root, limit=1)["items"][0]["run_id"] == second["run_id"]
    page = read_series(root, first["run_id"], "events", offset=1, limit=1)
    assert page["items"] == [{"kind":"close"}]
    assert page["total"] == 2
    assert os.stat(root/first["run_id"]).st_mode & 0o777 == 0o700
    assert os.stat(root/first["run_id"]/"summary.json").st_mode & 0o777 == 0o600
    assert "not an activation" in (root/first["run_id"]/"summary.md").read_text()


def test_incomplete_and_modified_artifacts_are_not_published(tmp_path):
    root = tmp_path/"reports"
    item = publish_report(root, report(), now=NOW)
    orphan = root/("c"*32)
    orphan.mkdir()
    assert list_reports(root)["total"] == 1
    summary = root/item["run_id"]/"summary.json"
    summary.write_text("{}")
    assert list_reports(root)["total"] == 0
    with pytest.raises(ValueError):
        read_report(root, item["run_id"])


def test_path_traversal_symlinks_and_unknown_series_are_rejected(tmp_path):
    root = tmp_path/"reports"
    item = publish_report(root, report(), now=NOW)
    for run_id in ("../secret", "/etc/passwd", "x"*32):
        with pytest.raises(ValueError):
            read_report(root, run_id)
    (root/("d"*32)).symlink_to(root/item["run_id"], target_is_directory=True)
    with pytest.raises(ValueError):
        read_report(root, "d"*32)
    with pytest.raises(ValueError):
        read_series(root, item["run_id"], "../summary")


def test_default_report_directory_does_not_create_or_read_source_database(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("INTRADAY_REPLAY_REPORT_DIR", raising=False)
    expected = tmp_path/"aigorithmic-trading"/"reports"/"replay-v2"
    assert resolve_report_dir() == expected
    assert list_reports(expected) == {"items":[], "total":0, "offset":0, "limit":20}
    assert not expected.exists()
