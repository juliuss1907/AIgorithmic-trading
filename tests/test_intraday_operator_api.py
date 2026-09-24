from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from intraday.contracts import DecisionScope
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.runtime import process_pending_commands
from intraday.store import IntradayStore
from intraday.web import create_app


READ_HEADERS = {"Authorization": "Bearer read-token"}
ACTION_HEADERS = {"Authorization": "Bearer action-token"}


def _client(tmp_path, *, actions_enabled=True):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    store.save_parent_portfolio_state(
        ParentPortfolioState(
            mark_price=100_000,
            day_start_equity=10_000,
            high_water_mark=10_000,
            entries_paused=False,
            paper_active=True,
            updated_at=now,
        ),
        event_kind="initialized",
        actor="test",
    )
    app = create_app(
        database=database,
        operator_read_token="read-token",
        operator_action_token="action-token",
        operator_actions_enabled=actions_enabled,
    )
    return TestClient(app), store, now


def test_operator_read_api_is_authenticated_scoped_and_redacted(tmp_path):
    client, _, _ = _client(tmp_path)

    assert client.get("/api/operator/v1/snapshot").status_code == 401
    assert client.get(
        "/api/operator/v1/snapshot", headers=ACTION_HEADERS
    ).status_code == 401
    response = client.get("/api/operator/v1/snapshot", headers=READ_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "1"
    assert payload["mode"] == "paper"
    assert payload["portfolio"]["paper_active"] is True
    assert payload["risk"]["leverage"] == 3
    assert payload["controls"] == {
        "actions_enabled": True,
        "allowed_actions": ["pause", "resume"],
        "approval_ttl_seconds": 300,
    }
    lowered = response.text.lower()
    assert "api_key" not in lowered
    assert "control_token" not in lowered
    assert "provider-secrets" not in lowered


def test_no_trade_endpoint_aggregates_reason_codes_without_raw_model_context(tmp_path):
    client, store, now = _client(tmp_path)
    store.record_journal_signal(
        decision_id="decision-no-trade-1",
        timestamp=now,
        symbol="BTCUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        state_snapshot='{"price":100000}',
        raw_signals={"price": 100_000.0},
        jev_answers={"direction": {"choice": "Hold", "confidence": 0.9}},
        gate_passed=False,
        gate_reason="hold;low_confidence",
        rules_version="champion-v1",
        llm_thesis=None,
    )

    response = client.get(
        "/api/operator/v1/no-trade?scope=perp_intraday&window_minutes=60",
        headers=READ_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["evaluations"] == 1
    assert response.json()["rejected"] == 1
    assert response.json()["reason_codes"] == {
        "hold": 1,
        "low_confidence": 1,
    }
    assert "state_snapshot" not in response.text
    assert "jev_answers" not in response.text


def test_action_api_enforces_feature_flag_scope_and_two_step_approval(tmp_path):
    disabled, _, _ = _client(tmp_path / "disabled", actions_enabled=False)
    assert disabled.post(
        "/api/operator/v1/action-requests",
        json={"action": "pause"},
        headers={**ACTION_HEADERS, "Idempotency-Key": "telegram-100"},
    ).status_code == 503

    client, store, now = _client(tmp_path / "enabled")
    assert client.post(
        "/api/operator/v1/action-requests",
        json={"action": "pause"},
        headers={**READ_HEADERS, "Idempotency-Key": "telegram-100"},
    ).status_code == 401
    created = client.post(
        "/api/operator/v1/action-requests",
        json={"action": "pause"},
        headers={**ACTION_HEADERS, "Idempotency-Key": "telegram-100"},
    )

    assert created.status_code == 202
    request_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    assert store.list_commands(status="pending") == []

    approved = client.post(
        f"/api/operator/v1/action-requests/{request_id}/approve",
        headers=ACTION_HEADERS,
    )
    replay = client.post(
        f"/api/operator/v1/action-requests/{request_id}/approve",
        headers=ACTION_HEADERS,
    )

    assert approved.status_code == 202
    assert approved.json()["status"] == "approved"
    assert replay.status_code == 409

    process_pending_commands(store, now=now + timedelta(seconds=1))
    result = client.get(
        f"/api/operator/v1/action-requests/{request_id}", headers=ACTION_HEADERS
    )
    assert result.json()["status"] == "applied"
    assert result.json()["result"]["action"] == "pause"


def test_alert_endpoint_is_cursor_based_and_read_only(tmp_path):
    client, store, now = _client(tmp_path)
    slot = now + timedelta(minutes=1)
    assert store.claim_scheduler_run("paper_perp_numeric", slot, started_at=slot)
    store.finish_scheduler_run(
        "paper_perp_numeric",
        slot,
        status="error",
        error_code="ProviderTimeout",
        finished_at=slot,
    )

    first = client.get("/api/operator/v1/alerts?after_id=0", headers=READ_HEADERS)
    cursor = first.json()["next_cursor"]
    empty = client.get(
        f"/api/operator/v1/alerts?after_id={cursor}", headers=READ_HEADERS
    )

    assert first.status_code == 200
    assert first.json()["alerts"][0]["kind"] == "scheduler_error"
    assert empty.json()["alerts"] == []
    assert empty.json()["next_cursor"] == cursor
