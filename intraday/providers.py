"""Model-provider boundaries and deterministic offline stubs."""

from __future__ import annotations

import hashlib
import json
import socket
import time
import urllib.error
from datetime import datetime, timezone
from typing import Protocol

from intraday.contracts import (
    Direction,
    FeatureSnapshot,
    JevDecision,
    ModelCallRecord,
    ProviderRole,
    Regime,
    RiskLevel,
)
from intraday.provider_client import HttpResponse, Transport, http_transport
from intraday.provider_profiles import ProviderCredential
from intraday.store import IntradayStore


class DecisionProvider(Protocol):
    model_ref: str

    def decide(self, snapshot: FeatureSnapshot, tick_id: str, now) -> JevDecision: ...


class ProviderDecisionError(RuntimeError):
    """A redacted provider failure safe for logs and fallback handling."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class JevDecisionProvider:
    """Typed OpenRouter Decisions adapter with a small local circuit breaker."""

    def __init__(
        self,
        credential: ProviderCredential,
        *,
        store: IntradayStore,
        transport: Transport | None = None,
        clock=time.monotonic,
        timeout_seconds: float = 2.5,
    ):
        if credential.profile.role != ProviderRole.JEV:
            raise ValueError("Jev provider requires a jev profile")
        self.credential = credential
        self.store = store
        self._transport = transport or http_transport
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._consecutive_failures = 0
        self._open_until = 0.0
        self.model_ref = (
            f"{credential.profile.profile_id}@{credential.profile.fingerprint[:12]}"
        )

    @staticmethod
    def _questions() -> dict:
        return {
            "direction": {
                "type": "choice",
                "instructions": "Choose the appropriate BTC perpetual trading action now.",
                "criteria": {
                    item.value: f"The action is {item.value}." for item in Direction
                },
            },
            "regime": {
                "type": "choice",
                "instructions": "Classify the current short-horizon market regime.",
                "criteria": {
                    item.value: f"The regime is {item.value}." for item in Regime
                },
            },
            "toxic_flow": {
                "type": "noul",
                "instructions": "Does current order flow show manipulation or toxic flow?",
                "criteria": {
                    "true": "Order flow is likely toxic or manipulative.",
                    "false": "Order flow appears ordinary and tradeable.",
                },
            },
            "entry_quality": {
                "type": "score",
                "instructions": "Score the confluence and quality of a new entry.",
                "criteria": [
                    "No valid entry",
                    "Weak setup",
                    "Acceptable setup",
                    "Strong setup",
                    "Exceptional setup",
                ],
            },
            "risk_level": {
                "type": "choice",
                "instructions": "Classify the immediate trading risk.",
                "criteria": {
                    item.value: f"Immediate risk is {item.value}." for item in RiskLevel
                },
            },
        }

    @staticmethod
    def _state(snapshot: FeatureSnapshot) -> dict:
        return {
            "snapshot_id": snapshot.snapshot_id,
            "symbol": snapshot.symbol,
            "event_time": snapshot.event_time.isoformat(),
            "bid": snapshot.bid,
            "ask": snapshot.ask,
            "features": snapshot.features,
            "freshness": snapshot.freshness,
            "quality_flags": snapshot.quality_flags,
        }

    @staticmethod
    def _number(value, *, minimum: float = 0, maximum: float = 1) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected numeric Jev answer")
        result = float(value)
        if not minimum <= result <= maximum:
            raise ValueError("Jev answer outside expected range")
        return result

    @classmethod
    def _parse_answers(cls, payload: dict) -> tuple:
        answers = payload["answers"]
        direction_value = answers["direction"]["choice"]
        direction = Direction(direction_value)
        direction_confidence = cls._number(
            answers["direction"]["probabilities"][direction_value]
        )
        regime = Regime(answers["regime"]["choice"])
        toxic_flow = cls._number(answers["toxic_flow"]["noul"])
        entry_quality = cls._number(
            answers["entry_quality"]["score"], minimum=0, maximum=4
        ) + 1
        risk_level = RiskLevel(answers["risk_level"]["choice"])
        return (
            direction,
            direction_confidence,
            regime,
            toxic_flow,
            entry_quality,
            risk_level,
        )

    @staticmethod
    def _request_id(response: HttpResponse, payload: dict | None = None) -> str | None:
        headers = {name.lower(): value for name, value in response.headers.items()}
        value = headers.get("x-request-id") or headers.get("x-openrouter-request-id")
        if value is None and payload is not None:
            value = payload.get("id")
        return value if isinstance(value, str) and len(value) <= 200 else None

    @staticmethod
    def _http_error(status_code: int) -> str:
        return {
            400: "invalid_request",
            401: "auth_failed",
            402: "insufficient_credits",
            403: "access_denied",
            404: "not_found",
            408: "timeout",
            413: "payload_too_large",
            429: "rate_limited",
        }.get(
            status_code,
            "provider_unavailable" if status_code >= 500 else "http_error",
        )

    def _record(
        self,
        *,
        tick_id: str,
        status: str,
        started_at: datetime,
        latency_ms: int,
        request_hash: str,
        response_body: bytes | None = None,
        response_payload: dict | None = None,
        error_code: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        usage = response_payload.get("usage", {}) if response_payload else {}
        if not isinstance(usage, dict):
            usage = {}
        response_model = response_payload.get("model") if response_payload else None
        if not isinstance(response_model, str) or not 1 <= len(response_model) <= 200:
            response_model = self.credential.profile.model

        def token_count(*names):
            value = next((usage.get(name) for name in names if usage.get(name) is not None), None)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        raw_cost = usage.get("cost")
        cost = (
            float(raw_cost)
            if isinstance(raw_cost, (int, float))
            and not isinstance(raw_cost, bool)
            and raw_cost >= 0
            else None
        )
        completed_at = datetime.now(timezone.utc)
        if completed_at < started_at:
            completed_at = started_at
        call_id = hashlib.sha256(
            f"jev:{tick_id}:{self.credential.profile.fingerprint}:{status}:{error_code}".encode()
        ).hexdigest()[:32]
        self.store.record_model_call(
            ModelCallRecord(
                call_id=call_id,
                workflow="jev_decision",
                role=ProviderRole.JEV,
                profile_id=self.credential.profile.profile_id,
                profile_fingerprint=self.credential.profile.fingerprint,
                model=response_model,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                latency_ms=latency_ms,
                input_tokens=token_count("prompt_tokens", "input_tokens"),
                output_tokens=token_count("completion_tokens", "output_tokens"),
                cost_usd=cost,
                provider_request_id=provider_request_id,
                request_hash=request_hash,
                response_hash=(
                    hashlib.sha256(response_body).hexdigest()
                    if response_body is not None else None
                ),
                error_code=error_code,
            )
        )

    def _fail(
        self,
        code: str,
        *,
        tick_id: str,
        started_at: datetime,
        started_clock: float,
        request_hash: str,
        response: HttpResponse | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= 3:
            self._open_until = self._clock() + 60
        self._record(
            tick_id=tick_id,
            status="error",
            started_at=started_at,
            latency_ms=max(0, round((self._clock() - started_clock) * 1000)),
            request_hash=request_hash,
            response_body=response.body if response else None,
            error_code=code,
            provider_request_id=provider_request_id,
        )
        raise ProviderDecisionError(code)

    def decide(self, snapshot: FeatureSnapshot, tick_id: str, now) -> JevDecision:
        payload = {
            "model": self.credential.profile.model,
            "state": self._state(snapshot),
            "questions": self._questions(),
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        request_hash = hashlib.sha256(body).hexdigest()
        started_at = datetime.now(timezone.utc)
        started_clock = self._clock()
        if started_clock < self._open_until:
            self._record(
                tick_id=tick_id,
                status="error",
                started_at=started_at,
                latency_ms=0,
                request_hash=request_hash,
                error_code="circuit_open",
            )
            raise ProviderDecisionError("circuit_open")
        try:
            response = self._transport(
                url=self.credential.profile.base_url,
                headers={
                    "Authorization": f"Bearer {self.credential.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "AIgorithmic-Trading/0.1 jev-decision",
                },
                body=body,
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, socket.timeout):
            self._fail(
                "timeout", tick_id=tick_id, started_at=started_at,
                started_clock=started_clock, request_hash=request_hash,
            )
        except (urllib.error.URLError, OSError):
            self._fail(
                "network_error", tick_id=tick_id, started_at=started_at,
                started_clock=started_clock, request_hash=request_hash,
            )
        request_id = self._request_id(response)
        if not 200 <= response.status_code < 300:
            self._fail(
                self._http_error(response.status_code), tick_id=tick_id,
                started_at=started_at, started_clock=started_clock,
                request_hash=request_hash, response=response,
                provider_request_id=request_id,
            )
        try:
            if len(response.body) > 1_000_000:
                raise ValueError("response too large")
            response_payload = json.loads(response.body)
            if not isinstance(response_payload, dict):
                raise ValueError("response must be an object")
            parsed = self._parse_answers(response_payload)
            request_id = self._request_id(response, response_payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._fail(
                "invalid_response", tick_id=tick_id, started_at=started_at,
                started_clock=started_clock, request_hash=request_hash,
                response=response, provider_request_id=request_id,
            )
        latency_ms = max(0, round((self._clock() - started_clock) * 1000))
        self._record(
            tick_id=tick_id,
            status="success",
            started_at=started_at,
            latency_ms=latency_ms,
            request_hash=request_hash,
            response_body=response.body,
            response_payload=response_payload,
            provider_request_id=request_id,
        )
        self._consecutive_failures = 0
        self._open_until = 0
        decision_id = hashlib.sha256(
            f"{self.model_ref}:{tick_id}:{snapshot.checksum}".encode()
        ).hexdigest()[:24]
        return JevDecision(
            decision_id=decision_id,
            tick_id=tick_id,
            snapshot_id=snapshot.snapshot_id,
            direction=parsed[0],
            direction_confidence=parsed[1],
            regime=parsed[2],
            toxic_flow=parsed[3],
            entry_quality=parsed[4],
            risk_level=parsed[5],
            model_ref=self.model_ref,
            created_at=now,
            latency_ms=latency_ms,
            provider_request_id=request_id,
        )


class StubDecisionProvider:
    """Reproducible provider for exercising every downstream invariant offline."""

    model_ref = "stub/jev-v1"

    def __init__(
        self,
        *,
        direction: Direction = Direction.HOLD,
        confidence: float = 0.91,
        regime: Regime = Regime.SIDEWAYS,
        toxic_flow: float = 0.10,
        entry_quality: float = 4,
        risk_level: RiskLevel = RiskLevel.LOW,
    ):
        self.direction = direction
        self.confidence = confidence
        self.regime = regime
        self.toxic_flow = toxic_flow
        self.entry_quality = entry_quality
        self.risk_level = risk_level

    def decide(self, snapshot: FeatureSnapshot, tick_id: str, now) -> JevDecision:
        decision_id = hashlib.sha256(
            f"{self.model_ref}:{tick_id}:{snapshot.checksum}".encode()
        ).hexdigest()[:24]
        return JevDecision(
            decision_id=decision_id,
            tick_id=tick_id,
            snapshot_id=snapshot.snapshot_id,
            direction=self.direction,
            direction_confidence=self.confidence,
            regime=self.regime,
            toxic_flow=self.toxic_flow,
            entry_quality=self.entry_quality,
            risk_level=self.risk_level,
            model_ref=self.model_ref,
            created_at=now,
            latency_ms=0,
        )
