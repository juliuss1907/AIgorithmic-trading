"""Model-provider boundaries and deterministic offline stubs."""

from __future__ import annotations

import hashlib
from typing import Protocol

from intraday.contracts import (
    Direction,
    FeatureSnapshot,
    JevDecision,
    Regime,
    RiskLevel,
)


class DecisionProvider(Protocol):
    model_ref: str

    def decide(self, snapshot: FeatureSnapshot, tick_id: str, now) -> JevDecision: ...


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
