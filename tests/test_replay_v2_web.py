from datetime import datetime, timezone
import sqlite3

from fastapi.testclient import TestClient

from intraday.replay_v2.artifacts import publish_report
from intraday.replay_v2.engine import simulate
from intraday.web import create_app
from test_replay_v2_spot import inputs, bar


def setup(tmp_path):
    root = tmp_path/"reports"
    config, data = inputs([bar(22, 110, 111, 109, 110)])
    report = simulate(config, data)
    item = publish_report(root, report, now=datetime(2026,10,1,tzinfo=timezone.utc))
    database = tmp_path/"source.sqlite"
    client = TestClient(create_app(database=database, replay_report_dir=root))
    return client, database, root, item


def dump(path):
    with sqlite3.connect(path) as c:
        return list(c.iterdump())


def test_replay_list_detail_and_paginated_series_are_read_only(tmp_path):
    client, database, root, item = setup(tmp_path)
    before = dump(database)
    listing = client.get("/api/replay/runs").json()
    assert listing["total"] == 1
    detail = client.get("/api/replay/runs/"+item["run_id"])
    assert detail.status_code == 200
    assert detail.json()["activation_allowed"] is False
    assert "report_directory" not in detail.text
    events = client.get(f"/api/replay/runs/{item['run_id']}/events?limit=1")
    assert len(events.json()["items"]) == 1
    assert client.post("/api/replay/runs", json={}).status_code == 405
    assert client.get("/api/replay/runs?limit=101").status_code == 422
    assert client.get(f"/api/replay/runs/{item['run_id']}/events?limit=1001").status_code == 422
    assert client.get("/api/replay/runs/invalid").status_code == 404
    assert dump(database) == before


def test_replay_pages_have_utc_plus_seven_limits_and_no_activation_controls(tmp_path):
    client, database, root, item = setup(tmp_path)
    listing = client.get("/replay")
    detail = client.get("/replay/"+item["run_id"])
    assert listing.status_code == detail.status_code == 200
    assert "01/10/2026 07:00:00 ICT" in listing.text
    assert "Replay v2" in listing.text
    assert "UTC+7" in detail.text
    assert "spot_jev_filter_not_replayed" in detail.text
    assert "Net PnL" in detail.text
    assert "Equity" in detail.text
    assert "entry" in detail.text
    assert 'method="post"' not in detail.text
    assert 'href="/replay"' in client.get("/assets").text


def test_empty_and_missing_reports_are_explicit_and_do_not_create_report_root(tmp_path):
    root = tmp_path/"not-created"
    client = TestClient(create_app(database=tmp_path/"source.sqlite", replay_report_dir=root))
    assert "Chưa có report" in client.get("/replay").text
    assert client.get("/replay/"+"a"*32).status_code == 404
    assert not root.exists()
