"""Bounded loopback client for the AIGT Operator API."""

from __future__ import annotations

import json
import os
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_ID = re.compile(r"^opreq_[a-f0-9]{24}$")


class OperatorClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _default_transport(method, url, headers, body, timeout):
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise OperatorClientError(f"http_{error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise OperatorClientError("unavailable") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise OperatorClientError("response_too_large")
    return payload


class OperatorClient:
    def __init__(
        self,
        *,
        base_url: str,
        read_token: str | None,
        action_token: str | None,
        timeout_seconds: float = 5,
        transport: Callable | None = None,
    ):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("AIGT Operator API must use an HTTP loopback URL")
        self.base_url = base_url.rstrip("/")
        self.read_token = read_token
        self.action_token = action_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _default_transport

    @classmethod
    def from_environment(cls) -> "OperatorClient":
        return cls(
            base_url=os.getenv("AIGT_OPERATOR_URL", "http://127.0.0.1:8081"),
            read_token=os.getenv("AIGT_OPERATOR_READ_TOKEN") or None,
            action_token=os.getenv("AIGT_OPERATOR_ACTION_TOKEN") or None,
        )

    def _request(self, method: str, path: str, *, scope: str, body=None, headers=None):
        token = self.read_token if scope == "read" else self.action_token
        if not token:
            raise OperatorClientError(f"{scope}_credential_unavailable")
        encoded = None
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if body is not None:
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        raw = self.transport(
            method,
            f"{self.base_url}{path}",
            request_headers,
            encoded,
            self.timeout_seconds,
        )
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
            raise OperatorClientError("invalid_response") from error
        if not isinstance(parsed, dict):
            raise OperatorClientError("invalid_response")
        return parsed

    def snapshot(self):
        return self._request("GET", "/api/operator/v1/snapshot", scope="read")

    def no_trade(self, scope: str, window_minutes: int = 60):
        if scope not in {"spot_daily", "perp_intraday"}:
            raise ValueError("scope must be spot_daily or perp_intraday")
        if not 5 <= window_minutes <= 1440:
            raise ValueError("window_minutes must be between 5 and 1440")
        query = urlencode({"scope": scope, "window_minutes": window_minutes})
        return self._request("GET", f"/api/operator/v1/no-trade?{query}", scope="read")

    def alerts(self, after_id: int = 0):
        if after_id < 0:
            raise ValueError("after_id must not be negative")
        return self._request(
            "GET", f"/api/operator/v1/alerts?after_id={after_id}", scope="read"
        )

    def create_action(self, action: str, *, idempotency_key: str):
        if action not in {"pause", "resume"}:
            raise ValueError("action must be pause or resume")
        return self._request(
            "POST",
            "/api/operator/v1/action-requests",
            scope="action",
            body={"action": action},
            headers={"Idempotency-Key": idempotency_key},
        )

    def action(self, request_id: str):
        self._validate_request_id(request_id)
        return self._request(
            "GET", f"/api/operator/v1/action-requests/{request_id}", scope="action"
        )

    def approve(self, request_id: str):
        self._validate_request_id(request_id)
        return self._request(
            "POST",
            f"/api/operator/v1/action-requests/{request_id}/approve",
            scope="action",
        )

    def cancel(self, request_id: str):
        self._validate_request_id(request_id)
        return self._request(
            "POST",
            f"/api/operator/v1/action-requests/{request_id}/cancel",
            scope="action",
        )

    @staticmethod
    def _validate_request_id(request_id: str) -> None:
        if not REQUEST_ID.fullmatch(request_id):
            raise ValueError("invalid operator request id")
