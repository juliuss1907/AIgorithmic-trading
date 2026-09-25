import io
import json
import os
import sqlite3
import stat
import sys
from argparse import Namespace
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
from intraday.provider_connect import _choose_option, _masked_api_key, _read_api_key
from intraday.runtime import process_pending_commands


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
        "analyst_reports", "market_theses", "rule_evaluations",
        "signals", "trades", "open_trade_context",
        "operator_action_requests", "operator_action_events",
        "operator_alerts",
    } <= provider_tables
    assert version == "17"


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


def test_secret_store_can_initialize_a_precreated_empty_mount_file(tmp_path):
    path = tmp_path / "providers.toml"
    path.touch(mode=0o600)

    ProviderSecretStore(path).upsert(profile(), "private-key")

    assert ProviderSecretStore(path).get("jev-openrouter").api_key == "private-key"


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


def test_connect_jev_openrouter_tests_and_activates_generated_profile(
    monkeypatch, capsys, tmp_path
):
    database = tmp_path / "intraday.sqlite"
    secrets_file = tmp_path / "providers.toml"
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(
        "intraday.provider_connect._masked_api_key",
        lambda prompt: prompts.append(prompt) or "openrouter-private-key",
        raising=False,
    )

    class SuccessfulPreflight:
        def test(self, credential):
            from intraday.provider_client import ProviderPreflightResult

            assert credential.profile.role == ProviderRole.JEV
            assert credential.profile.kind == ProviderKind.OPENROUTER_DECISIONS
            assert credential.profile.base_url == (
                "https://openrouter.ai/api/alpha/decisions"
            )
            assert credential.profile.model == "typesafe/jev-1.13"
            assert credential.api_key == "openrouter-private-key"
            return ProviderPreflightResult(status="ok", latency_ms=17)

    monkeypatch.setattr(
        "intraday.provider_connect.ProviderPreflightClient", SuccessfulPreflight
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "intraday", "connect", "jev", "openrouter",
            "--database", str(database),
            "--secrets-file", str(secrets_file),
        ],
    )

    main()

    output = capsys.readouterr().out
    assert output == "provider connected\n"
    assert "openrouter-private-key" not in output
    assert prompts == [
        "Provider API key: ",
        "Model ID [typesafe/jev-1.13]: ",
    ]
    assignment = IntradayStore(database).provider_assignment(ProviderRole.JEV)
    assert assignment["profile_id"].startswith("jev-openrouter-")


def test_connect_jev_custom_prompts_for_systemone_endpoint(monkeypatch, capsys, tmp_path):
    database = tmp_path / "intraday.sqlite"
    secrets_file = tmp_path / "providers.toml"
    answers = iter(["https://jev.example.com/v1/systemone", "custom-jev-v2"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(
        "intraday.provider_connect._masked_api_key",
        lambda _prompt: "custom-private-key",
        raising=False,
    )

    class SuccessfulPreflight:
        def test(self, credential):
            from intraday.provider_client import ProviderPreflightResult

            assert credential.profile.kind == ProviderKind.SYSTEMONE_COMPATIBLE
            assert credential.profile.base_url == "https://jev.example.com/v1/systemone"
            assert credential.profile.model == "custom-jev-v2"
            return ProviderPreflightResult(status="ok", latency_ms=23)

    monkeypatch.setattr(
        "intraday.provider_connect.ProviderPreflightClient", SuccessfulPreflight
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "intraday", "connect", "jev", "custom-provider",
            "--database", str(database),
            "--secrets-file", str(secrets_file),
        ],
    )

    main()

    assert capsys.readouterr().out == "provider connected\n"


def test_connect_jev_menu_defaults_and_selects_typesafe(monkeypatch, capsys, tmp_path):
    database = tmp_path / "intraday.sqlite"
    secrets_file = tmp_path / "providers.toml"
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(
        "intraday.provider_connect.toolkit_choice",
        lambda *args, **kwargs: "typesafe",
        raising=False,
    )
    monkeypatch.setattr(
        "intraday.provider_connect._masked_api_key", lambda _prompt: "typesafe-private-key"
    )

    class SuccessfulPreflight:
        def test(self, credential):
            from intraday.provider_client import ProviderPreflightResult

            assert credential.profile.kind == ProviderKind.TYPESAFE_SYSTEMONE
            assert credential.profile.model == "jev-latest"
            return ProviderPreflightResult(status="ok", latency_ms=11)

    monkeypatch.setattr(
        "intraday.provider_connect.ProviderPreflightClient", SuccessfulPreflight
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "intraday", "connect", "jev",
            "--database", str(database),
            "--secrets-file", str(secrets_file),
        ],
    )

    main()

    assert capsys.readouterr().out == "provider connected\n"


def test_masked_api_key_uses_star_password_prompt(monkeypatch):
    calls = []

    def fake_prompt(message, **options):
        calls.append((message, options))
        return "private-key"

    monkeypatch.setattr("intraday.provider_connect.toolkit_prompt", fake_prompt)

    assert _masked_api_key("Provider API key: ") == "private-key"
    assert calls[0][0] == "Provider API key: "
    assert calls[0][1]["is_password"] is True


def test_connect_menu_uses_arrow_choice_with_openrouter_default(monkeypatch):
    calls = []

    def fake_choice(message, **options):
        calls.append((message, options))
        return "typesafe"

    monkeypatch.setattr(
        "intraday.provider_connect.toolkit_choice", fake_choice, raising=False
    )

    assert _choose_option(ProviderRole.JEV) == "typesafe"
    assert calls == [
        (
            "Choose Jev provider:",
            {
                "options": [
                    ("openrouter", "OpenRouter"),
                    ("typesafe", "TypeSafe"),
                    ("custom-provider", "Custom provider"),
                ],
                "default": "openrouter",
                "bottom_toolbar": "↑/↓ move · Enter select · Ctrl+C cancel",
            },
        )
    ]


def test_connect_menu_reports_keyboard_cancellation(monkeypatch):
    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "intraday.provider_connect.toolkit_choice", cancel, raising=False
    )

    with pytest.raises(SystemExit, match="connection cancelled"):
        _choose_option(ProviderRole.JEV)


def test_api_key_prompt_reports_keyboard_cancellation(monkeypatch):
    def cancel(_message):
        raise KeyboardInterrupt

    monkeypatch.setattr("intraday.provider_connect._masked_api_key", cancel)

    with pytest.raises(SystemExit, match="connection cancelled"):
        _read_api_key(Namespace(api_key_stdin=False))


@pytest.mark.parametrize(
    ("option", "url", "expected_kind"),
    [
        (
            "anthropic-compatible",
            "https://models.example.com/v1/messages",
            ProviderKind.ANTHROPIC_COMPATIBLE,
        ),
        (
            "openai-compatible",
            "https://models.example.com/v1/chat/completions",
            ProviderKind.OPENAI_COMPATIBLE,
        ),
    ],
)
def test_connect_llm_selects_compatible_wire_protocol(
    option, url, expected_kind, monkeypatch, capsys, tmp_path
):
    database = tmp_path / f"{option}.sqlite"
    secrets_file = tmp_path / f"{option}.toml"
    answers = iter([url, "model-v1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(
        "intraday.provider_connect._masked_api_key",
        lambda _prompt: "llm-private-key",
        raising=False,
    )

    class SuccessfulPreflight:
        def test(self, credential):
            from intraday.provider_client import ProviderPreflightResult

            assert credential.profile.role == ProviderRole.LLM
            assert credential.profile.kind == expected_kind
            expected_url = (
                "https://models.example.com/v1"
                if option == "openai-compatible"
                else url
            )
            assert credential.profile.base_url == expected_url
            return ProviderPreflightResult(status="ok", latency_ms=31)

    monkeypatch.setattr(
        "intraday.provider_connect.ProviderPreflightClient", SuccessfulPreflight
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "intraday", "connect", "llm", option,
            "--database", str(database),
            "--secrets-file", str(secrets_file),
        ],
    )

    main()

    assert capsys.readouterr().out == "provider connected\n"


def test_connect_failure_does_not_persist_or_replace_active_provider(
    monkeypatch, capsys, tmp_path
):
    database = tmp_path / "intraday.sqlite"
    secrets_file = tmp_path / "providers.toml"
    store = IntradayStore(database)
    current = profile()
    ProviderSecretStore(secrets_file).upsert(current, "existing-private-key")
    store.sync_provider_profile(current)
    store.record_provider_test(
        current.profile_id, status="ok", tested_at=NOW, latency_ms=10
    )
    store.activate_provider(
        ProviderRole.JEV, current.profile_id, actor="test", now=NOW
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(
        "intraday.provider_connect._masked_api_key",
        lambda _prompt: "rejected-private-key",
        raising=False,
    )

    class FailedPreflight:
        def test(self, _credential):
            from intraday.provider_client import ProviderPreflightResult

            return ProviderPreflightResult(
                status="error", latency_ms=19, error_code="auth_failed"
            )

    monkeypatch.setattr(
        "intraday.provider_connect.ProviderPreflightClient", FailedPreflight
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "intraday", "connect", "jev", "openrouter",
            "--database", str(database),
            "--secrets-file", str(secrets_file),
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 1
    output = capsys.readouterr().out
    assert output == "API error\n"
    assert "rejected-private-key" not in output
    assert [item.profile_id for item in ProviderSecretStore(secrets_file).list_profiles()] == [
        current.profile_id
    ]
    assert store.provider_assignment(ProviderRole.JEV)["profile_id"] == current.profile_id


def test_connect_rejects_insecure_custom_url_before_preflight(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: {
            "Provider URL: ": "http://jev.example.com/v1/systemone",
            "Model ID: ": "custom-jev",
        }[prompt],
    )
    monkeypatch.setattr(
        "intraday.provider_connect._masked_api_key", lambda _prompt: "private-key"
    )
    monkeypatch.setattr(
        "intraday.provider_connect.ProviderPreflightClient",
        lambda: pytest.fail("invalid URL must not reach preflight"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "intraday", "connect", "jev", "custom-provider",
            "--database", str(tmp_path / "intraday.sqlite"),
            "--secrets-file", str(tmp_path / "providers.toml"),
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 1
    assert capsys.readouterr().out == "Invalid url\n"


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


def test_typesafe_native_preflight_uses_systemone_wire_contract():
    requests = []

    def transport(**request):
        requests.append(request)
        return HttpResponse(
            status_code=200,
            headers={"x-request-id": "typesafe-request-1"},
            body=b'{"answers":{"reachable":{"noul":1.0}}}',
        )

    from intraday.provider_profiles import ProviderCredential

    current = ProviderProfile.create(
        profile_id="jev-typesafe",
        role=ProviderRole.JEV,
        kind=ProviderKind.TYPESAFE_SYSTEMONE,
        base_url="https://api.typesafe.ai/v1/systemone",
        model="jev-latest",
        credential_version="credential-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    result = ProviderPreflightClient(transport=transport).test(
        ProviderCredential(current, "typesafe-private")
    )

    assert result.status == "ok"
    assert requests[0]["url"] == "https://api.typesafe.ai/v1/systemone"
    payload = json.loads(requests[0]["body"])
    assert payload["model"] == "jev-latest"
    assert payload["questions"]["reachable"]["type"] == "noul"


def test_systemone_compatible_preflight_uses_custom_endpoint():
    requests = []

    def transport(**request):
        requests.append(request)
        return HttpResponse(
            status_code=200,
            headers={},
            body=b'{"answers":{"reachable":{"noul":1.0}}}',
        )

    from intraday.provider_profiles import ProviderCredential

    current = ProviderProfile.create(
        profile_id="jev-custom",
        role=ProviderRole.JEV,
        kind=ProviderKind.SYSTEMONE_COMPATIBLE,
        base_url="https://jev.example.com/v1/systemone",
        model="custom-jev-v2",
        credential_version="credential-v1",
        created_at=NOW,
        updated_at=NOW,
    )

    result = ProviderPreflightClient(transport=transport).test(
        ProviderCredential(current, "custom-private")
    )

    assert result.status == "ok"
    assert requests[0]["url"] == "https://jev.example.com/v1/systemone"
    assert json.loads(requests[0]["body"])["model"] == "custom-jev-v2"


def test_anthropic_preflight_uses_messages_wire_contract():
    requests = []

    def transport(**request):
        requests.append(request)
        return HttpResponse(
            status_code=200,
            headers={"request-id": "anthropic-request-1"},
            body=b'{"content":[{"type":"text","text":"ok"}]}',
        )

    from intraday.provider_profiles import ProviderCredential

    current = ProviderProfile.create(
        profile_id="llm-anthropic",
        role=ProviderRole.LLM,
        kind=ProviderKind.ANTHROPIC_MESSAGES,
        base_url="https://api.anthropic.com/v1/messages",
        model="claude-sonnet-4-5",
        credential_version="credential-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    result = ProviderPreflightClient(transport=transport).test(
        ProviderCredential(current, "anthropic-private")
    )

    assert result.status == "ok"
    request = requests[0]
    assert request["url"] == "https://api.anthropic.com/v1/messages"
    assert request["headers"]["x-api-key"] == "anthropic-private"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in request["headers"]
    payload = json.loads(request["body"])
    assert payload["messages"] == [{"role": "user", "content": "Reply with: ok"}]
    assert payload["max_tokens"] == 1


@pytest.mark.parametrize(
    ("role", "kind", "base_url"),
    [
        (ProviderRole.LLM, ProviderKind.TYPESAFE_SYSTEMONE,
         "https://api.typesafe.ai/v1/systemone"),
        (ProviderRole.LLM, ProviderKind.SYSTEMONE_COMPATIBLE,
         "https://jev.example.com/v1/systemone"),
        (ProviderRole.JEV, ProviderKind.ANTHROPIC_MESSAGES,
         "https://api.anthropic.com/v1/messages"),
        (ProviderRole.JEV, ProviderKind.ANTHROPIC_COMPATIBLE,
         "https://models.example.com/v1/messages"),
        (ProviderRole.JEV, ProviderKind.OPENAI_COMPATIBLE,
         "https://api.example.com/v1"),
    ],
)
def test_provider_profile_rejects_protocol_role_mismatch(role, kind, base_url):
    with pytest.raises(ValueError, match="role"):
        ProviderProfile.create(
            profile_id="wrong-role",
            role=role,
            kind=kind,
            base_url=base_url,
            model="example-model",
            credential_version="credential-v1",
            created_at=NOW,
            updated_at=NOW,
        )


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


def test_worker_applies_queued_provider_test_and_activation(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    secrets = ProviderSecretStore(tmp_path / "providers.toml")
    current = profile()
    store.sync_provider_profile(current)
    secrets.upsert(current, "private-key")
    store.enqueue_command(
        "web:test-1",
        "provider_test",
        NOW,
        actor="dashboard",
        payload={"profile_id": current.profile_id},
    )

    class SuccessfulPreflight:
        def test(self, credential):
            from intraday.provider_client import ProviderPreflightResult

            assert credential.api_key == "private-key"
            return ProviderPreflightResult(status="ok", latency_ms=25)

    process_pending_commands(
        store,
        now=NOW,
        secret_store=secrets,
        preflight_client=SuccessfulPreflight(),
    )
    tested = store.list_commands()[0]
    assert tested["status"] == "applied"
    assert "private-key" not in str(tested)

    store.enqueue_command(
        "web:activate-1",
        "provider_activate",
        NOW,
        actor="dashboard",
        payload={"role": "jev", "profile_id": current.profile_id},
    )
    process_pending_commands(store, now=NOW, secret_store=secrets)

    assert store.provider_assignment(ProviderRole.JEV)["profile_id"] == current.profile_id
