from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from intraday.execution.contracts import AccountRef
from intraday.execution.journal import ExecutionJournal
from intraday.execution.perp_control import route_digest
from intraday.store import IntradayStore
from intraday.web import create_app


def setup_control(tmp_path):
    now = datetime.now(timezone.utc)
    source = tmp_path/"source.sqlite"
    store = IntradayStore(source)
    store.register_asset("ETHUSDT", market="perp", now=now)
    route = {"symbol":"ETHUSDT", "market":"perp", "venue":"bnb", "environment":"demo",
             "instrument":"ETHUSDT", "scan_id":"fixture", "updated_at":now.isoformat()}
    with store._connect() as c:
        c.execute("INSERT INTO asset_venue_routes VALUES (?,?,?,?,?,?,?)", tuple(route.values()))
    account = AccountRef(venue="binance", environment="demo", account_id="fixture")
    journal = ExecutionJournal(tmp_path/"execution.sqlite")
    info = {"status":"verified", "symbol":"ETHUSDT", "venue":"bnb", "environment":"demo",
            "account":account.key, "margin_mode":"ISOLATED", "actual_leverage":3,
            "configured_leverage":3, "setting_revision":0, "route_digest":route_digest(route),
            "observed_at":now.isoformat(), "positions":[], "open_orders":0,
            "change_blockers":[], "aigt_state":"not_activated", "market":"USD-M USDT Perpetual"}
    journal.save_perp_snapshot(account, "ETHUSDT", info)
    client = TestClient(create_app(database=source, control_token="fixture-token", execution_database=journal.path))
    preview = {"account": account.key, "symbol":"ETHUSDT", "previous":3, "target":5,
               "revision":0, "route_digest":info["route_digest"], "observed_at":info["observed_at"]}
    return client, journal, preview


def test_private_snapshot_and_confirmation_queue(tmp_path):
    client, journal, preview = setup_control(tmp_path)
    auth = {"Authorization":"Bearer fixture-token", "Idempotency-Key":"confirm-web-1"}
    assert client.get("/api/perp/ETH").status_code == 401
    snapshot = client.get("/api/perp/ETH", headers=auth)
    assert snapshot.json()["actual_leverage"] == 3
    assert snapshot.headers["Cache-Control"] == "no-store"
    response = client.post("/api/perp/ETH/leverage-requests", headers=auth, json=preview)
    assert response.status_code == 202, response.text
    request = response.json()
    assert request["status"] == "queued"
    assert client.post("/api/perp/ETH/leverage-requests", headers=auth, json=preview).json() == request
    assert client.get("/api/perp/ETH/leverage-requests/"+request["id"], headers=auth).json() == request
    assert journal.perp_setting(AccountRef.from_key(preview["account"]), "ETHUSDT")["leverage"] == 3
    assert not journal.portfolio(AccountRef.from_key(preview["account"]))
    assert client.post("/api/perp/ETH/leverage-requests", headers=auth, json={**preview,"target":4}).status_code == 409


def test_unavailable_controller_and_out_of_range_do_not_queue(tmp_path):
    client, journal, preview = setup_control(tmp_path)
    auth = {"Authorization":"Bearer fixture-token", "Idempotency-Key":"invalid-web-1"}
    assert client.post("/api/perp/ETH/leverage-requests", headers=auth, json={**preview,"target":11}).status_code == 422
    assert client.post("/api/perp/ETH/leverage-requests", headers=auth, json={**preview,"previous":4}).status_code == 409
    missing = TestClient(create_app(database=tmp_path/"other.sqlite", control_token="fixture-token"))
    assert missing.get("/api/perp/ETH", headers=auth).status_code == 503
    assert not journal.settings_requests(AccountRef.from_key(preview["account"]))


def test_settings_worker_consumes_confirmed_request_with_no_order_adapter(tmp_path):
    from intraday.execution.control_worker import run_control_cycle
    from intraday.execution.source import EvidenceSource
    from test_perp_control import Venue
    client, journal, preview = setup_control(tmp_path)
    auth = {"Authorization":"Bearer fixture-token", "Idempotency-Key":"worker-confirm-1"}
    item = client.post("/api/perp/ETH/leverage-requests", headers=auth, json=preview).json()
    account = AccountRef.from_key(preview["account"])
    credentials = type("Credentials", (), {"account_ref":account})()
    venue = Venue()
    result = run_control_cycle(EvidenceSource(tmp_path/"source.sqlite"), journal, credentials, venue_factory=lambda symbol:venue)
    assert result["orders_enabled"] is False
    assert result["requests"][0]["status"] == "verified"
    assert venue.changes == [5]
    assert journal.settings_request(item["id"])["status"] == "verified"
    assert client.get("/api/perp/ETH", headers=auth).json()["configured_leverage"] == 5


def test_same_context_refresh_does_not_invalidate_fresh_confirmation(tmp_path):
    client, journal, preview = setup_control(tmp_path)
    account = AccountRef.from_key(preview["account"])
    info = journal.perp_snapshots()[0]
    info["observed_at"] = (datetime.fromisoformat(preview["observed_at"])+timedelta(milliseconds=1)).isoformat()
    journal.save_perp_snapshot(account, "ETHUSDT", info)
    auth = {"Authorization":"Bearer fixture-token", "Idempotency-Key":"refresh-confirm-1"}
    assert client.post("/api/perp/ETH/leverage-requests", headers=auth, json=preview).status_code == 202


def test_old_preview_cannot_use_new_snapshot_to_bypass_expiry(tmp_path):
    client, journal, preview = setup_control(tmp_path)
    preview["observed_at"] = (datetime.now(timezone.utc)-timedelta(seconds=61)).isoformat()
    auth = {"Authorization":"Bearer fixture-token", "Idempotency-Key":"expired-confirm-1"}
    assert client.post("/api/perp/ETH/leverage-requests", headers=auth, json=preview).status_code == 409
    assert not journal.settings_requests(AccountRef.from_key(preview["account"]))


def test_changed_route_makes_projection_stale_and_blocks_confirmation(tmp_path):
    client, journal, preview = setup_control(tmp_path)
    with IntradayStore(tmp_path/"source.sqlite")._connect() as c:
        c.execute("UPDATE asset_venue_routes SET scan_id='changed' WHERE symbol='ETHUSDT'")
    auth = {"Authorization":"Bearer fixture-token", "Idempotency-Key":"route-changed-1"}
    assert client.get("/api/perp/ETH", headers=auth).json()["stale"] is True
    assert client.post("/api/perp/ETH/leverage-requests", headers=auth, json=preview).status_code == 409
    assert not journal.settings_requests(AccountRef.from_key(preview["account"]))
