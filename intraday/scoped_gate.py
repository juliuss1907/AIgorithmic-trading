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
    SPOT_SCOPES,
)


def _spot_jev_reasons(decision, rule):
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
    return tuple(reasons)


def _perp_setup2_jev_reasons(decision, rule):
    if decision.direction not in {Direction.SELL, Direction.STRONG_SELL}:
        return ("setup2_requires_jev_sell",)
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
    return tuple(reasons)


def jev_advice(rule, answers_or_decision) -> dict:
    """Jev-only verdict of the existing gate, without portfolio exposure or risk state."""
    if answers_or_decision is None:
        return dict(direction=None, confidence=None, would_allow=False, reasons=["jev_unavailable"])
    parameters = getattr(rule, "parameters", rule)
    decision = answers_or_decision
    if isinstance(decision, dict):
        from types import SimpleNamespace
        from intraday.providers import JevDecisionProvider
        values = JevDecisionProvider._parse_answers({"answers": decision})
        decision = SimpleNamespace(**dict(zip(("direction", "direction_confidence", "regime", "toxic_flow",
                                               "entry_quality", "risk_level"), values)))
    reasons = (_spot_jev_reasons(decision, parameters) if isinstance(parameters, SpotRuleParameters)
               else _perp_setup2_jev_reasons(decision, parameters))
    return dict(direction=decision.direction.value, confidence=decision.direction_confidence,
                would_allow=not reasons, reasons=list(reasons))


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
            if scope in SPOT_SCOPES
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
        decision: JevDecision | None,
        rule: SpotRuleParameters,
        *,
        donchian_entry: bool,
        size_multiplier: float = 1,
        scope: DecisionScope = DecisionScope.SPOT_DAILY,
        projected_isolated_margin_pct: float | None = None,
        advisory: bool = False,
    ) -> PortfolioAuthorization:
        if scope not in SPOT_SCOPES:
            raise ValueError("spot entry requires a Spot scope")
        if not donchian_entry:
            return self._deny(state, scope, ("donchian_entry_not_triggered",))
        if advisory and (rule.entry_profile != "setup2_slots_v1" or scope != DecisionScope.SPOT_4H):
            raise ValueError("advisory entries require the slot Demo Spot 4h profile")
        if not advisory and (reasons := _spot_jev_reasons(decision, rule)):
            return self._deny(state, scope, reasons)
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
            state, scope=scope, target_notional=target, projected_isolated_margin_pct=projected_isolated_margin_pct
        )

    def perp_entry(
        self,
        state: ParentPortfolioState,
        decision: JevDecision,
        rule: PerpRuleParameters,
        *,
        projected_isolated_margin_pct: float | None = None,
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
            state, scope=scope, target_notional=target, projected_isolated_margin_pct=projected_isolated_margin_pct
        )

    def perp_setup2_entry(
        self,
        state: ParentPortfolioState,
        decision: JevDecision | None,
        rule: PerpRuleParameters,
        *,
        setup_entry: bool,
        size_multiplier: float,
        projected_isolated_margin_pct: float | None = None,
        advisory: bool = False,
    ) -> PortfolioAuthorization:
        """Setup-2 short (ADR-006): the rule picks the side; Jev must confirm SELL or STRONG_SELL."""
        scope = DecisionScope.PERP_INTRADAY
        if not setup_entry:
            return self._deny(state, scope, ("no_setup2_short_setup",))
        if advisory and rule.entry_profile != "setup2_slots_v1":
            raise ValueError("advisory entries require the slot Demo profile")
        if not advisory and (reasons := _perp_setup2_jev_reasons(decision, rule)):
            return self._deny(state, scope, reasons)
        policy = self.coordinator.policy
        sleeve_target = state.equity*policy.perp_budget_pct*policy.perp_sleeve_notional_pct*size_multiplier
        gross_room = max(0, state.equity*policy.max_gross_exposure_pct-state.spot_notional)
        return self.coordinator.authorize_target(
            state, scope=scope, target_notional=-min(sleeve_target, gross_room),
            projected_isolated_margin_pct=projected_isolated_margin_pct)

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
