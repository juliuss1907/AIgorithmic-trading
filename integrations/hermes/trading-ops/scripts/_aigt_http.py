"""Read-only HTTP helper for Hermes cron scripts."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 256 * 1024


class OperatorReadClient:
    def __init__(self):
        self.base_url = os.getenv(
            "AIGT_OPERATOR_URL", "http://127.0.0.1:8081"
        ).rstrip("/")
        self.token = os.getenv("AIGT_OPERATOR_READ_TOKEN") or ""
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("operator_url_not_loopback")
        if not self.token:
            raise RuntimeError("operator_read_credential_unavailable")

    def _get(self, path: str) -> dict:
        request = Request(
            f"{self.base_url}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=5) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise RuntimeError(f"operator_http_{error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise RuntimeError("operator_unavailable") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError("operator_response_too_large")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError("operator_invalid_response") from error
        if not isinstance(payload, dict):
            raise RuntimeError("operator_invalid_response")
        return payload

    def snapshot(self) -> dict:
        return self._get("/api/operator/v1/snapshot")

    def alerts(self, after_id: int) -> dict:
        if after_id < 0:
            raise ValueError("alert cursor must not be negative")
        return self._get(f"/api/operator/v1/alerts?after_id={after_id}")
