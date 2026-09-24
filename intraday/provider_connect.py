"""Interactive provider connection flow for the AIGT CLI."""

from __future__ import annotations

import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from prompt_toolkit import prompt as toolkit_prompt

from intraday.contracts import ProviderKind, ProviderProfile, ProviderRole
from intraday.provider_client import ProviderPreflightClient
from intraday.provider_profiles import (
    ProviderCredential,
    ProviderSecretStore,
    validate_provider_api_key,
)
from intraday.store import IntradayStore


def _masked_api_key(message: str) -> str:
    return toolkit_prompt(message, is_password=True)


def _read_api_key(arguments) -> str:
    value = (
        sys.stdin.readline().rstrip("\r\n")
        if arguments.api_key_stdin
        else _masked_api_key("Provider API key: ")
    )
    try:
        return validate_provider_api_key(value)
    except ValueError as error:
        raise SystemExit(str(error)) from error


def _choose_option(role: ProviderRole) -> str:
    if role == ProviderRole.JEV:
        options = ("openrouter", "typesafe", "custom-provider")
        print("Choose Jev provider:\n  1. OpenRouter\n  2. TypeSafe\n  3. Custom provider")
    else:
        options = ("anthropic-compatible", "openai-compatible")
        print("Choose LLM protocol:\n  1. Anthropic-compatible\n  2. OpenAI-compatible")
    selected = input("Selection [1]: ").strip() or "1"
    try:
        index = int(selected) - 1
        if not 0 <= index < len(options):
            raise IndexError
        return options[index]
    except (ValueError, IndexError) as error:
        raise SystemExit("invalid provider selection") from error


def _openai_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    suffix = "/chat/completions"
    return normalized[:-len(suffix)] if normalized.endswith(suffix) else normalized


def connect_provider(arguments, *, database: Path, secrets_file: Path) -> None:
    role = ProviderRole(arguments.connect_role)
    option = arguments.provider_option or _choose_option(role)

    if role == ProviderRole.JEV and option == "openrouter":
        kind = ProviderKind.OPENROUTER_DECISIONS
        base_url = "https://openrouter.ai/api/alpha/decisions"
        api_key = _read_api_key(arguments)
        model = input("Model ID [typesafe/jev-1.13]: ").strip() or "typesafe/jev-1.13"
    elif role == ProviderRole.JEV and option == "typesafe":
        kind = ProviderKind.TYPESAFE_SYSTEMONE
        base_url = "https://api.typesafe.ai/v1/systemone"
        api_key = _read_api_key(arguments)
        model = input("Model ID [jev-latest]: ").strip() or "jev-latest"
    elif role == ProviderRole.JEV:
        kind = ProviderKind.SYSTEMONE_COMPATIBLE
        base_url = input("Provider URL: ").strip()
        api_key = _read_api_key(arguments)
        model = input("Model ID: ").strip()
    else:
        kind = (
            ProviderKind.ANTHROPIC_COMPATIBLE
            if option == "anthropic-compatible"
            else ProviderKind.OPENAI_COMPATIBLE
        )
        base_url = input("Provider URL: ").strip()
        if kind == ProviderKind.OPENAI_COMPATIBLE:
            base_url = _openai_base_url(base_url)
        api_key = _read_api_key(arguments)
        model = input("Model ID: ").strip()

    if not model:
        raise SystemExit("model ID is required")
    now = datetime.now(timezone.utc)
    try:
        profile = ProviderProfile.create(
            profile_id=f"{role.value}-{option}-{secrets.token_hex(4)}",
            role=role,
            kind=kind,
            base_url=base_url,
            model=model,
            credential_version=secrets.token_hex(16),
            created_at=now,
            updated_at=now,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    result = ProviderPreflightClient().test(ProviderCredential(profile, api_key))
    output = {
        "profile_id": profile.profile_id,
        "role": role.value,
        "kind": kind.value,
        "status": result.status,
        "latency_ms": result.latency_ms,
        "active": False,
    }
    if result.status != "ok":
        output["error_code"] = result.error_code
        print(json.dumps(output, indent=2))
        raise SystemExit(1)

    secret_store = ProviderSecretStore(secrets_file)
    store = IntradayStore(database)
    secret_store.upsert(profile, api_key, replace=False)
    store.sync_provider_profile(profile)
    store.record_provider_test(
        profile.profile_id,
        status="ok",
        tested_at=now,
        latency_ms=result.latency_ms,
    )
    store.activate_provider(role, profile.profile_id, actor="cli", now=now)
    output["active"] = True
    print(json.dumps(output, indent=2))
