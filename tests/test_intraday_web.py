import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from intraday.contracts import ProviderKind, ProviderProfile, ProviderRole
from intraday.store import IntradayStore
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


def test_provider_and_analyst_read_apis_are_redacted(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    profile = ProviderProfile.create(
        profile_id="jev-openrouter",
        role=ProviderRole.JEV,
        kind=ProviderKind.OPENROUTER_DECISIONS,
        base_url="https://openrouter.ai/api/alpha/decisions",
        model="typesafe/jev-1.13",
        credential_version="credential-v1",
        created_at=now,
        updated_at=now,
    )
    store.sync_provider_profile(profile)
    client = TestClient(create_app(database=database))

    providers = client.get("/api/providers")
    analysts = client.get("/api/analysts")

    assert providers.status_code == 200
    assert providers.json()["profiles"][0]["profile_id"] == profile.profile_id
    assert "api_key" not in providers.text
    assert "secret" not in providers.text.lower()
    assert analysts.json() == {"reports": {}, "thesis": None, "daily_cost_usd": 0.0}


def test_provider_mutations_are_authenticated_idempotent_commands(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    profile = ProviderProfile.create(
        profile_id="llm-main",
        role=ProviderRole.LLM,
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://api.example.com/v1",
        model="example/model",
        credential_version="credential-v1",
        created_at=now,
        updated_at=now,
    )
    store.sync_provider_profile(profile)
    client = TestClient(create_app(database=database, control_token="control-token"))
    headers = {
        "Authorization": "Bearer control-token",
        "Idempotency-Key": "provider-test-1",
    }

    assert client.post(f"/api/providers/{profile.profile_id}/tests").status_code == 401
    first = client.post(f"/api/providers/{profile.profile_id}/tests", headers=headers)
    repeated = client.post(f"/api/providers/{profile.profile_id}/tests", headers=headers)

    assert first.status_code == 202
    assert first.json() == repeated.json()
    assert first.json()["kind"] == "provider_test"
    assert json.loads(first.json()["payload_json"]) == {"profile_id": profile.profile_id}

    activate = client.put(
        "/api/provider-assignments/llm",
        json={"profile_id": profile.profile_id},
        headers={**headers, "Idempotency-Key": "provider-activate-1"},
    )
    assert activate.status_code == 202
    assert activate.json()["kind"] == "provider_activate"
