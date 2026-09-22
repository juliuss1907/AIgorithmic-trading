import io
import json
import os
import sqlite3
import stat
import sys
from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import (
    ModelCallRecord,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
)
from intraday.store import IntradayStore
from intraday.provider_profiles import ProviderSecretStore
from intraday.provider_client import (
    HttpResponse,
    ProviderPreflightClient,
)
from intraday.__main__ import main


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
    assert {
        "provider_profiles", "provider_assignments", "model_calls",
        "analyst_reports", "market_theses",
    } <= provider_tables
    assert version == "3"


def test_secret_store_writes_mode_0600_and_never_exposes_key_in_repr(tmp_path):
    path = tmp_path / "secrets" / "providers.toml"
    secret_store = ProviderSecretStore(path)
    current = profile()

    secret_store.upsert(current, "sk-or-private")
    loaded = secret_store.get(current.profile_id)

    assert loaded.profile == current
    assert loaded.api_key == "sk-or-private"
    assert "sk-or-private" not in repr(loaded)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_secret_store_rejects_group_readable_files_and_symlinks(tmp_path):
    path = tmp_path / "providers.toml"
    secret_store = ProviderSecretStore(path)
    secret_store.upsert(profile(), "private-key")
    path.chmod(0o640)

    with pytest.raises(PermissionError, match="0600"):
        secret_store.list_profiles()

    target = tmp_path / "target.toml"
    target.write_text("schema_version = 1\nprofiles = []\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "link.toml"
    link.symlink_to(target)
    with pytest.raises(PermissionError, match="symlink"):
        ProviderSecretStore(link).list_profiles()


def test_provider_cli_add_and_list_redacts_api_key(monkeypatch, capsys, tmp_path):
    database = tmp_path / "intraday.sqlite"
    secrets_file = tmp_path / "providers.toml"
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-or-private\n"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "intraday", "provider", "add", "jev-openrouter",
            "--database", str(database),
            "--secrets-file", str(secrets_file),
            "--role", "jev",
            "--kind", "openrouter-decisions",
            "--model", "typesafe/jev-1.13",
            "--api-key-stdin",
        ],
    )

    main()
    added_output = capsys.readouterr().out

    assert "sk-or-private" not in added_output
    assert json.loads(added_output)["profile_id"] == "jev-openrouter"
    assert "sk-or-private" not in str(IntradayStore(database).list_provider_profiles())

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "intraday", "provider", "list",
            "--database", str(database),
            "--secrets-file", str(secrets_file),
        ],
    )
    main()
    listed_output = capsys.readouterr().out

    assert "sk-or-private" not in listed_output
    assert json.loads(listed_output)[0]["has_secret"] is True


def test_jev_preflight_uses_decisions_wire_contract_without_leaking_key():
    requests = []

    def transport(**request):
        requests.append(request)
        return HttpResponse(
            status_code=200,
            headers={"x-request-id": "request-1"},
            body=json.dumps(
                {"answers": {"reachable": {"noul": 1.0, "confidence": 1.0}}}
            ).encode(),
        )

    from intraday.provider_profiles import ProviderCredential

    result = ProviderPreflightClient(transport=transport).test(
        ProviderCredential(profile(), "sk-or-private")
    )

    assert result.status == "ok"
    assert result.provider_request_id == "request-1"
    assert requests[0]["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert requests[0]["headers"]["Authorization"] == "Bearer sk-or-private"
    payload = json.loads(requests[0]["body"])
    assert payload["model"] == "typesafe/jev-1.13"
    assert payload["questions"]["reachable"]["type"] == "noul"
    assert "sk-or-private" not in repr(result)


def test_llm_preflight_uses_openai_compatible_chat_completions():
    requests = []

    def transport(**request):
        requests.append(request)
        return HttpResponse(
            status_code=200,
            headers={},
            body=b'{"choices":[{"message":{"content":"ok"}}]}',
        )

    from intraday.provider_profiles import ProviderCredential

    current = profile("llm-main", role=ProviderRole.LLM)
    result = ProviderPreflightClient(transport=transport).test(
        ProviderCredential(current, "llm-private")
    )

    assert result.status == "ok"
    assert requests[0]["url"] == "https://api.example.com/v1/chat/completions"
    payload = json.loads(requests[0]["body"])
    assert payload["model"] == "example/model"
    assert payload["max_tokens"] == 1


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(401, "auth_failed"), (402, "insufficient_credits"), (429, "rate_limited")],
)
def test_preflight_normalizes_provider_errors(status_code, error_code):
    def transport(**request):
        return HttpResponse(
            status_code=status_code,
            headers={},
            body=b'{"error":{"message":"provider rejected request"}}',
        )

    from intraday.provider_profiles import ProviderCredential

    result = ProviderPreflightClient(transport=transport).test(
        ProviderCredential(profile(), "never-return-this")
    )

    assert result.status == "error"
    assert result.error_code == error_code
    assert "never-return-this" not in repr(result)


def test_provider_cli_test_activate_and_deactivate(monkeypatch, capsys, tmp_path):
    database = tmp_path / "intraday.sqlite"
    secrets_file = tmp_path / "providers.toml"
    current = profile()
    ProviderSecretStore(secrets_file).upsert(current, "private-key")
    IntradayStore(database).sync_provider_profile(current)

    class SuccessfulPreflight:
        def test(self, credential):
            from intraday.provider_client import ProviderPreflightResult

            return ProviderPreflightResult(
                status="ok",
                latency_ms=42,
                provider_request_id="request-safe",
            )

    monkeypatch.setattr(
        "intraday.__main__.ProviderPreflightClient", SuccessfulPreflight
    )
    common = ["--database", str(database), "--secrets-file", str(secrets_file)]

    monkeypatch.setattr(
        sys, "argv", ["intraday", "provider", "test", current.profile_id, *common]
    )
    main()
    tested = json.loads(capsys.readouterr().out)
    assert tested == {
        "profile_id": current.profile_id,
        "status": "ok",
        "latency_ms": 42,
        "error_code": None,
        "provider_request_id": "request-safe",
    }

    monkeypatch.setattr(
        sys,
        "argv",
        ["intraday", "provider", "activate", "jev", current.profile_id, *common],
    )
    main()
    assert json.loads(capsys.readouterr().out)["profile_id"] == current.profile_id
    assert IntradayStore(database).provider_assignment(ProviderRole.JEV) is not None

    monkeypatch.setattr(
        sys, "argv", ["intraday", "provider", "deactivate", "jev", *common]
    )
    main()
    assert json.loads(capsys.readouterr().out) == {"role": "jev", "active": False}
    assert IntradayStore(database).provider_assignment(ProviderRole.JEV) is None
