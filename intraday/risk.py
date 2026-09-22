"""Deterministic risk policy and final authorization gate."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from intraday.contracts import (
    CrossVenueAssessment,
    Direction,
    FeatureSnapshot,
    GateDecision,
    JevDecision,
    PositionSnapshot,
    RiskLevel,
    RiskState,
    RuleParameters,
)


@dataclass(frozen=True)
class HardRiskPolicy:
    leverage: int = 3
    max_tranches: int = 2
    max_tranche_risk_pct: float = 0.0025
    max_position_risk_pct: float = 0.005
    max_tranche_notional_pct: float = 0.25
    max_position_notional_pct: float = 0.50
    max_initial_margin_pct: float = 0.20
    daily_loss_limit_pct: float = 0.015
    max_drawdown_pct: float = 0.08
    min_stop_distance_pct: float = 0.0075
    max_stop_distance_pct: float = 0.03
    min_liquidation_buffer_pct: float = 0.15
    emergency_liquidation_buffer_pct: float = 0.10
    entry_cooldown_seconds: int = 60
    confidence_threshold: float = 0.85
    entry_quality_min: float = 3
    toxic_flow_max: float = 0.30

    def tranche_notional(self, equity: float, stop_distance: float) -> float:
        if equity <= 0:
            raise ValueError("equity must be positive")
        if not self.min_stop_distance_pct <= stop_distance <= self.max_stop_distance_pct:
            raise ValueError("stop distance is outside the immutable policy")
        risk_limited = equity * self.max_tranche_risk_pct / stop_distance
        return min(equity * self.max_tranche_notional_pct, risk_limited)

    def initial_margin(self, notional: float) -> float:
        if notional < 0:
            raise ValueError("notional cannot be negative")
        return notional / self.leverage


class RiskGate:
    def __init__(
        self,
        policy: HardRiskPolicy | None = None,
        rule_id: str = "rule-v1",
        rule: RuleParameters | None = None,
    ):
        self.policy = policy or HardRiskPolicy()
        self.rule_id = rule_id
        self.rule = rule or RuleParameters()

    @staticmethod
    def _model_target(decision: JevDecision, position: PositionSnapshot) -> int:
        current = position.tranches
        direct_targets = {
            Direction.STRONG_BUY: 2,
            Direction.BUY: 1,
            Direction.SELL: -1,
            Direction.STRONG_SELL: -2,
        }
        if decision.direction == Direction.HOLD:
            return current
        if decision.direction == Direction.TAKE_PROFIT:
            if position.unrealized_pnl <= 0 or current == 0:
                return current
            return current - 1 if current > 0 else current + 1
        requested = direct_targets[decision.direction]
        if current and (current > 0) != (requested > 0):
            return 0
        return requested

    @staticmethod
    def _gate_id(decision_id: str, evaluated_at: datetime, target: int) -> str:
        raw = f"{decision_id}:{evaluated_at.isoformat()}:{target}".encode()
        return hashlib.sha256(raw).hexdigest()[:24]

    def _result(
        self,
        decision: JevDecision,
        now: datetime,
        outcome: str,
        target: int,
        reasons: tuple[str, ...],
        notional: float = 0,
        cross_venue: CrossVenueAssessment | None = None,
        cross_venue_mode: str = "off",
        effective_entry_quality: float | None = None,
    ) -> GateDecision:
        return GateDecision(
            gate_id=self._gate_id(decision.decision_id, now, target),
            decision_id=decision.decision_id,
            rule_id=self.rule_id,
            outcome=outcome,
            target_tranches=target,
            authorized_notional=notional,
            reason_codes=reasons,
            evaluated_at=now,
            cross_venue_mode=cross_venue_mode,
            cross_venue_status=cross_venue.status if cross_venue else None,
            cross_venue_applied=cross_venue is not None and cross_venue_mode == "active",
            entry_quality_adjustment=(cross_venue.entry_quality_delta if cross_venue else 0),
            effective_entry_quality=effective_entry_quality,
            notional_multiplier=(cross_venue.notional_multiplier if cross_venue else 1),
        )

    def evaluate(
        self,
        snapshot: FeatureSnapshot,
        decision: JevDecision,
        risk: RiskState,
        position: PositionSnapshot,
        now: datetime,
        *,
        stop_distance: float | None = None,
        cross_venue: CrossVenueAssessment | None = None,
        cross_venue_mode: str = "off",
    ) -> GateDecision:
        if cross_venue_mode not in {"off", "shadow", "active"}:
            raise ValueError("invalid cross-venue mode")
        current = position.tranches
        stop_distance = self.rule.stop_distance_pct if stop_distance is None else stop_distance
        cross_active = cross_venue is not None and cross_venue_mode == "active"
        effective_entry_quality = min(5, max(
            1,
            decision.entry_quality + (cross_venue.entry_quality_delta if cross_active else 0),
        ))

        def result(outcome, target, reasons, notional=0):
            return self._result(
                decision,
                now,
                outcome,
                target,
                reasons,
                notional,
                cross_venue=cross_venue,
                cross_venue_mode=cross_venue_mode,
                effective_entry_quality=effective_entry_quality,
            )

        if current and position.entry_price is not None:
            signed_return = (
                (position.mark_price / position.entry_price - 1)
                * (1 if current > 0 else -1)
            )
            if signed_return <= -stop_distance:
                return result("reduce_only", 0, ("stop_loss",))

        if position.liquidation_buffer is not None:
            if position.liquidation_buffer < self.policy.emergency_liquidation_buffer_pct:
                return result("reduce_only", 0, ("liquidation_buffer_emergency",))
            if position.liquidation_buffer < self.policy.min_liquidation_buffer_pct:
                target = current - 1 if current > 0 else current + 1
                return result("reduce_only", target, ("liquidation_buffer_low",))

        loss_reasons = []
        if risk.drawdown <= -self.policy.max_drawdown_pct:
            loss_reasons.append("drawdown_limit")
        if risk.daily_return <= -self.policy.daily_loss_limit_pct:
            loss_reasons.append("daily_loss_limit")
        if loss_reasons:
            return result("reduce_only" if current else "hold", 0, tuple(loss_reasons))

        if decision.model_ref == "fallback/hold-v1":
            return result("hold", current, ("provider_failure",))

        target = self._model_target(decision, position)
        reducing = abs(target) < abs(current)
        if reducing:
            return result("reduce_only", target, ())
        if target == current:
            return result("hold", target, ("target_unchanged",))

        reasons: list[str] = []
        if risk.halted:
            reasons.append(risk.halt_reason or "risk_halted")
        if risk.drawdown <= -self.policy.max_drawdown_pct:
            reasons.append("drawdown_limit")
        if risk.daily_return <= -self.policy.daily_loss_limit_pct:
            reasons.append("daily_loss_limit")
        if risk.news_pause_until and now < risk.news_pause_until:
            reasons.append("news_pause")
        snapshot_age = (now - snapshot.built_at).total_seconds()
        if (
            not all(snapshot.freshness.values())
            or snapshot.quality_flags
            or snapshot_age > 10
            or snapshot_age < -2
        ):
            reasons.append("stale_data")
        if decision.snapshot_id != snapshot.snapshot_id:
            reasons.append("snapshot_mismatch")
        confidence_threshold = max(
            self.policy.confidence_threshold, self.rule.confidence_threshold
        )
        entry_quality_min = max(self.policy.entry_quality_min, self.rule.entry_quality_min)
        toxic_flow_max = min(self.policy.toxic_flow_max, self.rule.toxic_flow_max)
        if decision.direction_confidence < confidence_threshold:
            reasons.append("low_confidence")
        if effective_entry_quality < entry_quality_min:
            reasons.append("low_entry_quality")
        if decision.toxic_flow > toxic_flow_max:
            reasons.append("toxic_flow")
        if decision.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            reasons.append("model_risk_high")
        if decision.regime not in self.rule.allowed_regimes:
            reasons.append("regime_not_allowed")
        if risk.last_entry_at and now < risk.last_entry_at + timedelta(
            seconds=self.policy.entry_cooldown_seconds
        ):
            reasons.append("entry_cooldown")
        if cross_active and cross_venue.notional_multiplier == 0:
            reasons.extend(cross_venue.reason_codes or ("cross_venue_stressed",))

        try:
            tranche_notional = self.policy.tranche_notional(risk.equity, stop_distance)
        except ValueError:
            reasons.append("invalid_stop_distance")
            tranche_notional = 0
        total_notional = abs(target) * tranche_notional
        if cross_active:
            total_notional *= cross_venue.notional_multiplier
        if total_notional > risk.equity * self.policy.max_position_notional_pct + 1e-9:
            reasons.append("position_notional_limit")
        if self.policy.initial_margin(total_notional) > (
            risk.equity * self.policy.max_initial_margin_pct + 1e-9
        ):
            reasons.append("initial_margin_limit")

        if reasons:
            return result("hold", current, tuple(dict.fromkeys(reasons)))
        return result("authorized", target, (), total_notional)
