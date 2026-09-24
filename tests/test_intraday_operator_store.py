from datetime import datetime, timedelta, timezone

import pytest

from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.runtime import process_pending_commands
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 24, 2, 0, tzinfo=timezone.utc)


def _initialized_store(tmp_path) -> IntradayStore:
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.save_parent_portfolio_state(
        ParentPortfolioState(
            mark_price=100_000,
            day_start_equity=10_000,
            high_water_mark=10_000,
            entries_paused=False,
            paper_active=True,
            updated_at=NOW,
        ),
        event_kind="initialized",
        actor="test",
    )
    return store


def test_operator_action_requires_a_second_step_before_queueing_command(tmp_path):
    store = _initialized_store(tmp_path)

    request = store.create_operator_action_request(
        request_id="opreq_pause_1",
        idempotency_key="telegram-update-100",
        action="pause",
        actor="hermes-trading-ops",
        requested_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert request["status"] == "pending"
    assert request["preview"]["entries_paused"] is False
    assert store.list_commands(status="pending") == []

    approved = store.approve_operator_action_request(
        "opreq_pause_1", actor="hermes-trading-ops", approved_at=NOW
    )

    assert approved["status"] == "approved"
    assert approved["command_id"] == "hermes:opreq_pause_1"
    assert store.list_commands(status="pending")[0]["kind"] == "portfolio_pause"

    process_pending_commands(store, now=NOW)

    applied = store.operator_action_request("opreq_pause_1", now=NOW)
    assert applied["status"] == "applied"
    assert store.load_parent_portfolio_state().entries_paused is True
    assert [event["event_type"] for event in store.list_operator_action_events()] == [
        "requested",
        "approved",
        "applied",
    ]


def test_operator_action_idempotency_rejects_a_different_payload(tmp_path):
    store = _initialized_store(tmp_path)
    first = store.create_operator_action_request(
        request_id="opreq_pause_1",
        idempotency_key="telegram-update-100",
        action="pause",
        actor="hermes-trading-ops",
        requested_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    repeated = store.create_operator_action_request(
        request_id="ignored-on-retry",
        idempotency_key="telegram-update-100",
        action="pause",
        actor="hermes-trading-ops",
        requested_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert repeated == first
    with pytest.raises(ValueError, match="idempotency key conflicts"):
        store.create_operator_action_request(
            request_id="opreq_resume_1",
            idempotency_key="telegram-update-100",
            action="resume",
            actor="hermes-trading-ops",
            requested_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )


def test_expired_cancelled_and_replayed_operator_actions_never_queue_twice(tmp_path):
    store = _initialized_store(tmp_path)
    store.create_operator_action_request(
        request_id="opreq_expired",
        idempotency_key="telegram-update-expired",
        action="pause",
        actor="hermes-trading-ops",
        requested_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    expired = store.operator_action_request(
        "opreq_expired", now=NOW + timedelta(minutes=6)
    )
    assert expired["status"] == "expired"
    with pytest.raises(ValueError, match="not pending"):
        store.approve_operator_action_request(
            "opreq_expired",
            actor="hermes-trading-ops",
            approved_at=NOW + timedelta(minutes=6),
        )

    store.create_operator_action_request(
        request_id="opreq_cancelled",
        idempotency_key="telegram-update-cancelled",
        action="resume",
        actor="hermes-trading-ops",
        requested_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    cancelled = store.cancel_operator_action_request(
        "opreq_cancelled", actor="hermes-trading-ops", cancelled_at=NOW
    )
    assert cancelled["status"] == "cancelled"

    store.create_operator_action_request(
        request_id="opreq_once",
        idempotency_key="telegram-update-once",
        action="pause",
        actor="hermes-trading-ops",
        requested_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    store.approve_operator_action_request(
        "opreq_once", actor="hermes-trading-ops", approved_at=NOW
    )
    with pytest.raises(ValueError, match="not pending"):
        store.approve_operator_action_request(
            "opreq_once", actor="hermes-trading-ops", approved_at=NOW
        )

    assert len(store.list_commands()) == 1


def test_operator_action_schema_migration_is_additive(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")

    assert store.schema_version() == 16
    assert store.list_operator_action_events() == []
