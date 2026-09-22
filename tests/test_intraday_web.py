from datetime import datetime, timezone

from fastapi.testclient import TestClient

from intraday.web import create_app


def test_dashboard_and_status_are_available_without_control_credentials(tmp_path):
    client = TestClient(create_app(database=tmp_path / "intraday.sqlite"))

    page = client.get("/")
    status = client.get("/api/status")

    assert page.status_code == 200
    assert "Intraday control room" in page.text
    assert "Paper only" in page.text
    assert "Hyperliquid" in page.text
    assert status.json()["mode"] == "paper"
    assert status.json()["counts"]["decisions"] == 0
    assert status.json()["cross_venue"]["venue"] == "hyperliquid"


def test_control_endpoint_is_token_protected_and_deduplicated(tmp_path):
    client = TestClient(
        create_app(database=tmp_path / "intraday.sqlite", control_token="correct-token")
    )
    body = {"command_id": "cmd-1", "kind": "pause_entries"}

    assert client.post("/api/commands", json=body).status_code == 401
    accepted = client.post(
        "/api/commands",
        json=body,
        headers={"Authorization": "Bearer correct-token"},
    )
    repeated = client.post(
        "/api/commands",
        json=body,
        headers={"Authorization": "Bearer correct-token"},
    )

    assert accepted.status_code == 202
    assert accepted.json() == repeated.json()
    assert accepted.json()["actor"] == "dashboard"


def test_decision_api_has_a_bounded_limit(tmp_path):
    client = TestClient(create_app(database=tmp_path / "intraday.sqlite"))

    assert client.get("/api/decisions?limit=0").status_code == 422
    assert client.get("/api/decisions?limit=1001").status_code == 422
    assert client.get("/api/decisions?limit=20").json() == []
