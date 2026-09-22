import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import (
    ModelCallRecord,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
)
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


def profile(
    profile_id: str = "jev-openrouter",
    *,
    role: ProviderRole = ProviderRole.JEV,
) -> ProviderProfile:
    return ProviderProfile.create(
        profile_id=profile_id,
        role=role,
        kind=(
            ProviderKind.OPENROUTER_DECISIONS
            if role == ProviderRole.JEV
            else ProviderKind.OPENAI_COMPATIBLE
        ),
        base_url=(
            "https://openrouter.ai/api/alpha/decisions"
            if role == ProviderRole.JEV
            else "https://api.example.com/v1"
        ),
        model="typesafe/jev-1.13" if role == ProviderRole.JEV else "example/model",
        credential_version="credential-v1",
        created_at=NOW,
        updated_at=NOW,
    )


def test_provider_profile_is_content_addressed_and_contains_no_secret_field():
    current = profile()

    assert len(current.fingerprint) == 64
    assert current.profile_id == "jev-openrouter"
    assert "secret" not in current.model_dump(mode="json")
    assert "api_key" not in current.model_dump(mode="json")


def test_store_requires_a_recent_successful_preflight_before_activation(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile()
    store.sync_provider_profile(current)

    with pytest.raises(ValueError, match="preflight"):
        store.activate_provider(
            ProviderRole.JEV,
            current.profile_id,
            actor="cli",
            now=NOW,
        )

    store.record_provider_test(
        current.profile_id,
        status="ok",
        tested_at=NOW,
        latency_ms=120,
    )
    assignment = store.activate_provider(
        ProviderRole.JEV,
        current.profile_id,
        actor="cli",
        now=NOW + timedelta(minutes=1),
    )

    assert assignment["profile_id"] == current.profile_id
    assert assignment["role"] == "jev"
    assert store.provider_assignment(ProviderRole.JEV) == assignment


def test_store_rejects_stale_preflight_and_role_mismatch(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile("llm-main", role=ProviderRole.LLM)
    store.sync_provider_profile(current)
    store.record_provider_test(
        current.profile_id,
        status="ok",
        tested_at=NOW,
        latency_ms=200,
    )

    with pytest.raises(ValueError, match="role"):
        store.activate_provider(ProviderRole.JEV, current.profile_id, actor="cli", now=NOW)
    with pytest.raises(ValueError, match="preflight"):
        store.activate_provider(
            ProviderRole.LLM,
            current.profile_id,
            actor="cli",
            now=NOW + timedelta(minutes=11),
        )


def test_model_call_telemetry_round_trips_without_request_or_secret_content(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile()
    store.sync_provider_profile(current)
    call = ModelCallRecord(
        call_id="call-1",
        workflow="jev_decision",
        role=ProviderRole.JEV,
        profile_id=current.profile_id,
        profile_fingerprint=current.fingerprint,
        model="typesafe/jev-1.13-20260917",
        status="success",
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=120),
        latency_ms=120,
        input_tokens=500,
        output_tokens=0,
        cost_usd=0.000021,
        provider_request_id="request-1",
        request_hash="a" * 64,
        response_hash="b" * 64,
    )

    store.record_model_call(call)

    assert store.list_model_calls(limit=10) == [call]


def test_existing_v1_database_is_migrated_additively(tmp_path):
    database = tmp_path / "intraday.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE commands (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                actor TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '1');
            """
        )

    IntradayStore(database)

    with sqlite3.connect(database) as connection:
        command_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(commands)")
        }
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        provider_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {"payload_json", "result_json", "error_code", "applied_at"} <= command_columns
    assert {"provider_profiles", "provider_assignments", "model_calls"} <= provider_tables
    assert version == "2"
