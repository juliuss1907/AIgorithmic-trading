"""Scoped AI entry gates layered behind deterministic portfolio risk."""

from __future__ import annotations

from intraday.contracts import (
    DecisionScope,
    Direction,
    JevDecision,
    PerpRuleParameters,
    RiskLevel,
    SpotRuleParameters,
)
from intraday.portfolio_coordinator import (
    ParentPortfolioCoordinator,
    ParentPortfolioState,
    PortfolioAuthorization,
)


class ScopedEntryGate:
    def __init__(self, coordinator: ParentPortfolioCoordinator | None = None):
        self.coordinator = coordinator or ParentPortfolioCoordinator()

    def _deny(
        self,
        state: ParentPortfolioState,
        scope: DecisionScope,
        reasons: tuple[str, ...],
    ) -> PortfolioAuthorization:
        current = (
            state.spot_notional
            if scope == DecisionScope.SPOT_DAILY
            else state.perp_notional
        )
        baseline = self.coordinator.authorize_target(
            state, scope=scope, target_notional=current
        )
        return baseline.model_copy(
            update={
                "allowed": False,
                "target_notional": current,
                "reduce_only": False,
                "reason_codes": reasons,
            }
        )

    def spot_entry(
        self,
        state: ParentPortfolioState,
        decision: JevDecision,
        rule: SpotRuleParameters,
        *,
        donchian_entry: bool,
        size_multiplier: float = 1,
    ) -> PortfolioAuthorization:
        scope = DecisionScope.SPOT_DAILY
        if not donchian_entry:
            return self._deny(state, scope, ("donchian_entry_not_triggered",))
        reasons = []
        if decision.direction not in {Direction.BUY, Direction.STRONG_BUY}:
            reasons.append("spot_long_only")
        if decision.direction_confidence < rule.jev_confidence_threshold:
            reasons.append("low_confidence")
        if decision.regime not in rule.allowed_regimes:
            reasons.append("regime_not_allowed")
        if decision.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            reasons.append("model_risk_high")
        if decision.toxic_flow > 0.30:
            reasons.append("toxic_flow")
        if reasons:
            return self._deny(state, scope, tuple(reasons))
        if not 0 <= size_multiplier <= 1:
            raise ValueError("spot size multiplier must be between zero and one")
        sleeve_target = (
            state.equity
            * self.coordinator.policy.spot_budget_pct
            * self.coordinator.policy.spot_sleeve_target_pct
            * size_multiplier
        )
        gross_room = max(
            0,
            state.equity * self.coordinator.policy.max_gross_exposure_pct
            - abs(state.perp_notional),
        )
        target = min(sleeve_target, gross_room)
        return self.coordinator.authorize_target(
            state, scope=scope, target_notional=target
        )

    def perp_entry(
        self,
        state: ParentPortfolioState,
        decision: JevDecision,
        rule: PerpRuleParameters,
    ) -> PortfolioAuthorization:
        scope = DecisionScope.PERP_INTRADAY
        current = state.perp_notional
        if decision.direction == Direction.HOLD:
            return self._deny(state, scope, ("hold",))
        if decision.direction == Direction.TAKE_PROFIT:
            return self.coordinator.authorize_target(
                state, scope=scope, target_notional=0
            )
        reasons = []
        if decision.direction_confidence < rule.confidence_threshold:
            reasons.append("low_confidence")
        if decision.entry_quality < rule.entry_quality_min:
            reasons.append("low_entry_quality")
        if decision.toxic_flow > rule.toxic_flow_max:
            reasons.append("toxic_flow")
        if decision.regime not in rule.allowed_regimes:
            reasons.append("regime_not_allowed")
        if decision.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            reasons.append("model_risk_high")
        if reasons:
            return self._deny(state, scope, tuple(reasons))
        side = 1 if decision.direction in {Direction.BUY, Direction.STRONG_BUY} else -1
        sleeve_target = (
            state.equity
            * self.coordinator.policy.perp_budget_pct
            * self.coordinator.policy.perp_sleeve_notional_pct
        )
        gross_room = max(
            0,
            state.equity * self.coordinator.policy.max_gross_exposure_pct
            - state.spot_notional,
        )
        target = side * min(sleeve_target, gross_room)
        return self.coordinator.authorize_target(
            state, scope=scope, target_notional=target
        )

    def deterministic_exit(
        self,
        state: ParentPortfolioState,
        scope: DecisionScope,
        reason: str,
    ) -> PortfolioAuthorization:
        result = self.coordinator.authorize_target(
            state, scope=scope, target_notional=0
        )
        return result.model_copy(
            update={
                "allowed": True,
                "target_notional": 0,
                "reduce_only": True,
                "reason_codes": (reason,),
            }
        )
