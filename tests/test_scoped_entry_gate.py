from datetime import datetime, timezone

from intraday.contracts import (
    DecisionScope,
    Direction,
    JevDecision,
    PerpRuleParameters,
    Regime,
    RiskLevel,
    SpotRuleParameters,
)
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.scoped_gate import ScopedEntryGate


NOW = datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc)


def state(**updates):
    values = {
        "mark_price": 100_000,
        "day_start_equity": 10_000,
        "high_water_mark": 10_000,
        "entries_paused": False,
        "paper_active": True,
        "updated_at": NOW,
    }
    values.update(updates)
    return ParentPortfolioState(**values)


def decision(direction=Direction.BUY, confidence=0.91, regime=Regime.TRENDING_UP):
    return JevDecision(
        decision_id="decision-1",
        tick_id="tick-1",
        snapshot_id="snapshot-1",
        direction=direction,
        direction_confidence=confidence,
        regime=regime,
        toxic_flow=0.1,
        entry_quality=4,
        risk_level=RiskLevel.LOW,
        model_ref="jev-native@fingerprint",
        created_at=NOW,
    )


def test_spot_entry_requires_both_donchian_setup_and_jev_approval():
    gate = ScopedEntryGate()

    no_setup = gate.spot_entry(
        state(), decision(), SpotRuleParameters(), donchian_entry=False
    )
    low_confidence = gate.spot_entry(
        state(), decision(confidence=0.8), SpotRuleParameters(), donchian_entry=True
    )
    approved = gate.spot_entry(
        state(), decision(), SpotRuleParameters(), donchian_entry=True
    )

    assert no_setup.allowed is False
    assert no_setup.reason_codes == ("donchian_entry_not_triggered",)
    assert low_confidence.allowed is False
    assert low_confidence.reason_codes == ("low_confidence",)
    assert approved.allowed is True
    assert approved.scope == DecisionScope.SPOT_DAILY
    assert approved.target_notional == 3_000


def test_spot_never_shorts_and_deterministic_exit_does_not_need_jev():
    gate = ScopedEntryGate()
    current = state(spot_quantity=0.02, spot_entry_price=90_000)

    sell_signal = gate.spot_entry(
        current,
        decision(direction=Direction.STRONG_SELL),
        SpotRuleParameters(),
        donchian_entry=True,
    )
    exit_ = gate.deterministic_exit(current, DecisionScope.SPOT_DAILY, "donchian_exit")

    assert sell_signal.allowed is False
    assert sell_signal.reason_codes == ("spot_long_only",)
    assert exit_.allowed is True
    assert exit_.reduce_only is True
    assert exit_.target_notional == 0
    assert exit_.reason_codes == ("donchian_exit",)


def test_perp_rule_and_jev_direction_feed_parent_risk_coordinator():
    gate = ScopedEntryGate()

    approved = gate.perp_entry(state(), decision(), PerpRuleParameters())
    toxic = gate.perp_entry(
        state(),
        decision().model_copy(update={"toxic_flow": 0.4}),
        PerpRuleParameters(),
    )

    assert approved.allowed is True
    assert approved.scope == DecisionScope.PERP_INTRADAY
    assert approved.target_notional == 2_000
    assert toxic.allowed is False
    assert toxic.reason_codes == ("toxic_flow",)
