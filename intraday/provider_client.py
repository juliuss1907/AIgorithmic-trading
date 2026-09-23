"""Minimal provider connectivity checks with redacted, normalized results."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping

from intraday.contracts import ProviderKind
from intraday.provider_profiles import ProviderCredential


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class ProviderPreflightResult:
    status: str
    latency_ms: int
    error_code: str | None = None
    provider_request_id: str | None = None


Transport = Callable[..., HttpResponse]


def http_transport(*, url: str, headers: dict[str, str], body: bytes, timeout: float):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                status_code=response.status,
                headers=dict(response.headers.items()),
                body=response.read(1_000_001),
            )
    except urllib.error.HTTPError as error:
        return HttpResponse(
            status_code=error.code,
            headers=dict(error.headers.items()) if error.headers else {},
            body=error.read(1_000_001),
        )


def _error_code(status_code: int) -> str:
    return {
        400: "invalid_request",
        401: "auth_failed",
        403: "access_denied",
        402: "insufficient_credits",
        404: "not_found",
        408: "timeout",
        413: "payload_too_large",
        429: "rate_limited",
    }.get(status_code, "provider_unavailable" if status_code >= 500 else "http_error")


class ProviderPreflightClient:
    def __init__(
        self,
        *,
        transport: Transport | None = None,
        timeout_seconds: float = 5.0,
    ):
        if timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")
        self._transport = transport or http_transport
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _request(credential: ProviderCredential) -> tuple[str, dict]:
        profile = credential.profile
        if profile.kind in {
            ProviderKind.OPENROUTER_DECISIONS,
            ProviderKind.TYPESAFE_SYSTEMONE,
        }:
            return profile.base_url, {
                "model": profile.model,
                "state": {"probe": "provider connectivity check"},
                "questions": {
                    "reachable": {
                        "type": "noul",
                        "instructions": "Is this a provider connectivity check?",
                        "criteria": {
                            "true": "The state explicitly describes a connectivity check.",
                            "false": "The state describes a different task.",
                        },
                    }
                },
            }
        if profile.kind == ProviderKind.ANTHROPIC_MESSAGES:
            return profile.base_url, {
                "model": profile.model,
                "messages": [{"role": "user", "content": "Reply with: ok"}],
                "max_tokens": 1,
            }
        return profile.base_url.rstrip("/") + "/chat/completions", {
            "model": profile.model,
            "messages": [{"role": "user", "content": "Reply with: ok"}],
            "max_tokens": 1,
            "temperature": 0,
        }

    @staticmethod
    def _valid_response(kind: ProviderKind, payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        if kind in {
            ProviderKind.OPENROUTER_DECISIONS,
            ProviderKind.TYPESAFE_SYSTEMONE,
        }:
            answer = payload.get("answers", {}).get("reachable", {})
            return isinstance(answer, dict) and isinstance(answer.get("noul"), (int, float))
        if kind == ProviderKind.ANTHROPIC_MESSAGES:
            content = payload.get("content")
            return (
                isinstance(content, list)
                and bool(content)
                and isinstance(content[0], dict)
                and content[0].get("type") == "text"
            )
        choices = payload.get("choices")
        return isinstance(choices, list) and bool(choices)

    @staticmethod
    def _headers(credential: ProviderCredential) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AIgorithmic-Trading/0.1 provider-preflight",
        }
        if credential.profile.kind == ProviderKind.ANTHROPIC_MESSAGES:
            headers.update(
                {
                    "x-api-key": credential.api_key,
                    "anthropic-version": "2023-06-01",
                }
            )
        else:
            headers["Authorization"] = f"Bearer {credential.api_key}"
        return headers

    def test(self, credential: ProviderCredential) -> ProviderPreflightResult:
        url, payload = self._request(credential)
        started = time.monotonic()
        request_id = None
        try:
            response = self._transport(
                url=url,
                headers=self._headers(credential),
                body=json.dumps(payload, separators=(",", ":")).encode(),
                timeout=self._timeout_seconds,
            )
            headers = {name.lower(): value for name, value in response.headers.items()}
            request_id = (
                headers.get("x-request-id")
                or headers.get("x-openrouter-request-id")
                or headers.get("request-id")
            )
            if not 200 <= response.status_code < 300:
                return ProviderPreflightResult(
                    status="error",
                    latency_ms=round((time.monotonic() - started) * 1000),
                    error_code=_error_code(response.status_code),
                    provider_request_id=request_id,
                )
            if len(response.body) > 1_000_000:
                raise ValueError("provider response too large")
            decoded = json.loads(response.body)
            if not self._valid_response(credential.profile.kind, decoded):
                raise ValueError("provider response does not match expected schema")
        except (TimeoutError, socket.timeout):
            error_code = "timeout"
        except urllib.error.URLError:
            error_code = "network_error"
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            error_code = "invalid_response"
        else:
            return ProviderPreflightResult(
                status="ok",
                latency_ms=round((time.monotonic() - started) * 1000),
                provider_request_id=request_id,
            )
        return ProviderPreflightResult(
            status="error",
            latency_ms=round((time.monotonic() - started) * 1000),
            error_code=error_code,
            provider_request_id=request_id,
        )
