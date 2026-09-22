from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import (
    CrossVenueAssessment,
    Direction,
    FeatureSnapshot,
    JevDecision,
    PositionSnapshot,
    Regime,
    RiskLevel,
    RiskState,
    RuleParameters,
)
from intraday.risk import HardRiskPolicy, RiskGate


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def snapshot(*, fresh=True, at=NOW):
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=at,
        built_at=at,
        bid=99_990,
        ask=100_010,
        features={"price": 100_000.0, "rsi14": 55.0},
        freshness={"book": fresh, "candles": fresh, "funding": fresh},
    )


def decision(direction=Direction.BUY, **overrides):
    values = {
        "decision_id": "decision-1",
        "tick_id": "tick-1",
        "snapshot_id": snapshot().snapshot_id,
        "direction": direction,
        "direction_confidence": 0.91,
        "regime": Regime.TRENDING_UP,
        "toxic_flow": 0.1,
        "entry_quality": 4.0,
        "risk_level": RiskLevel.LOW,
        "model_ref": "stub/jev-v1",
        "created_at": NOW,
    }
    values.update(overrides)
    return JevDecision(**values)


def position(tranches=0, *, unrealized_pnl=0.0, liquidation_buffer=None, mark_price=100_000):
    return PositionSnapshot(
        tranches=tranches,
        quantity=float(tranches),
        entry_price=100_000 if tranches else None,
        mark_price=mark_price,
        notional=abs(tranches) * mark_price,
        leverage=3,
        isolated_margin=abs(tranches) * 1_000 / 3,
        maintenance_margin=abs(tranches) * 4,
        liquidation_price=70_000 if tranches > 0 else (130_000 if tranches < 0 else None),
        liquidation_buffer=liquidation_buffer,
        funding=0,
        unrealized_pnl=unrealized_pnl,
    )


def state(**overrides):
    values = {
        "equity": 10_000,
        "high_water_mark": 10_000,
        "daily_return": 0,
        "drawdown": 0,
        "halted": False,
    }
    values.update(overrides)
    return RiskState(**values)


def test_position_sizing_uses_risk_and_notional_caps():
    policy = HardRiskPolicy()

    assert policy.tranche_notional(10_000, 0.01) == pytest.approx(2_500)
    assert policy.tranche_notional(10_000, 0.03) == pytest.approx(833.333333)
    assert policy.initial_margin(5_000) == pytest.approx(1_666.666667)


def test_strong_buy_targets_two_tranches_inside_hard_limits():
    gate = RiskGate().evaluate(
        snapshot(), decision(Direction.STRONG_BUY), state(), position(), NOW, stop_distance=0.01
    )

    assert gate.outcome == "authorized"
    assert gate.target_tranches == 2
    assert gate.authorized_notional == pytest.approx(5_000)
    assert gate.reason_codes == ()


def test_opposite_signal_closes_without_flipping_in_same_tick():
    gate = RiskGate().evaluate(
        snapshot(), decision(Direction.STRONG_SELL), state(), position(2), NOW
    )

    assert gate.outcome == "reduce_only"
    assert gate.target_tranches == 0


def test_news_pause_blocks_new_exposure_then_expires():
    paused = state(news_pause_until=NOW + timedelta(minutes=30))
    fresh = snapshot(at=NOW + timedelta(minutes=31))

    blocked = RiskGate().evaluate(snapshot(), decision(), paused, position(), NOW)
    resumed = RiskGate().evaluate(
        fresh,
        decision(snapshot_id=fresh.snapshot_id),
        paused,
        position(),
        NOW + timedelta(minutes=31),
    )

    assert blocked.outcome == "hold"
    assert "news_pause" in blocked.reason_codes
    assert resumed.outcome == "authorized"


def test_stale_data_and_high_risk_fail_closed_for_entries():
    stale = RiskGate().evaluate(snapshot(fresh=False), decision(), state(), position(), NOW)
    risky = RiskGate().evaluate(
        snapshot(), decision(risk_level=RiskLevel.HIGH), state(), position(), NOW
    )

    assert stale.outcome == "hold"
    assert "stale_data" in stale.reason_codes
    assert risky.outcome == "hold"
    assert "model_risk_high" in risky.reason_codes


def test_old_snapshot_is_rejected_even_when_source_flags_claim_freshness():
    old = snapshot().model_copy(update={"built_at": NOW - timedelta(seconds=20)})

    gate = RiskGate().evaluate(old, decision(), state(), position(), NOW)

    assert gate.outcome == "hold"
    assert "stale_data" in gate.reason_codes


def test_decision_cannot_be_replayed_against_a_different_snapshot():
    other = snapshot(at=NOW + timedelta(seconds=1))

    gate = RiskGate().evaluate(other, decision(), state(), position(), NOW + timedelta(seconds=1))

    assert gate.outcome == "hold"
    assert "snapshot_mismatch" in gate.reason_codes


def test_liquidation_guard_reduces_before_emergency_flatten():
    warning = RiskGate().evaluate(
        snapshot(), decision(Direction.HOLD), state(), position(2, liquidation_buffer=0.14), NOW
    )
    emergency = RiskGate().evaluate(
        snapshot(), decision(Direction.HOLD), state(), position(2, liquidation_buffer=0.09), NOW
    )

    assert warning.outcome == "reduce_only"
    assert warning.target_tranches == 1
    assert "liquidation_buffer_low" in warning.reason_codes
    assert emergency.target_tranches == 0
    assert "liquidation_buffer_emergency" in emergency.reason_codes


@pytest.mark.parametrize(
    ("risk", "reason"),
    [
        (state(daily_return=-0.015), "daily_loss_limit"),
        (state(drawdown=-0.08), "drawdown_limit"),
    ],
)
def test_loss_limits_force_reduce_only_flatten(risk, reason):
    gate = RiskGate().evaluate(snapshot(), decision(Direction.HOLD), risk, position(2), NOW)

    assert gate.outcome == "reduce_only"
    assert gate.target_tranches == 0
    assert reason in gate.reason_codes


def test_stop_loss_flattens_without_waiting_for_model_agreement():
    gate = RiskGate().evaluate(
        snapshot(), decision(Direction.STRONG_BUY), state(), position(1, mark_price=98_900), NOW
    )

    assert gate.outcome == "reduce_only"
    assert gate.target_tranches == 0
    assert gate.reason_codes == ("stop_loss",)


def test_provider_failure_cannot_block_a_hard_risk_exit():
    fallback = decision(
        Direction.HOLD,
        model_ref="fallback/hold-v1",
        direction_confidence=0,
        toxic_flow=1,
        entry_quality=1,
        risk_level=RiskLevel.CRITICAL,
    )

    gate = RiskGate().evaluate(
        snapshot(), fallback, state(), position(2, liquidation_buffer=0.09), NOW
    )

    assert gate.outcome == "reduce_only"
    assert gate.reason_codes == ("liquidation_buffer_emergency",)


def test_bounded_active_rule_can_tighten_but_not_loosen_entry_gate():
    gate = RiskGate(
        rule_id="candidate-1",
        rule=RuleParameters(
            confidence_threshold=0.95,
            allowed_regimes=(Regime.SIDEWAYS,),
        ),
    ).evaluate(snapshot(), decision(), state(), position(), NOW)

    assert gate.rule_id == "candidate-1"
    assert gate.outcome == "hold"
    assert set(gate.reason_codes) == {"low_confidence", "regime_not_allowed"}


def cross_assessment(status, delta, multiplier):
    return CrossVenueAssessment(
        status=status,
        entry_quality_delta=delta,
        notional_multiplier=multiplier,
        reason_codes=(f"cross_venue_{status}",),
        policy_version="cross-venue-v1",
        evaluated_at=NOW,
    )


def test_shadow_overlay_is_audited_but_does_not_change_gate_outcome():
    assessment = cross_assessment("confirming", 0.5, 1)

    gate = RiskGate().evaluate(
        snapshot(), decision(entry_quality=2.75), state(), position(), NOW,
        cross_venue=assessment, cross_venue_mode="shadow",
    )

    assert gate.outcome == "hold"
    assert "low_entry_quality" in gate.reason_codes
    assert gate.cross_venue_status == "confirming"
    assert gate.cross_venue_applied is False
    assert gate.entry_quality_adjustment == 0.5
    assert gate.effective_entry_quality == 2.75


def test_active_overlay_can_raise_quality_and_only_reduce_notional():
    confirming = RiskGate().evaluate(
        snapshot(), decision(entry_quality=2.75), state(), position(), NOW,
        cross_venue=cross_assessment("confirming", 0.5, 1), cross_venue_mode="active",
    )
    conflicting = RiskGate().evaluate(
        snapshot(), decision(entry_quality=4), state(), position(), NOW,
        cross_venue=cross_assessment("conflicting", -1, 0.5), cross_venue_mode="active",
    )

    assert confirming.outcome == "authorized"
    assert confirming.effective_entry_quality == 3.25
    assert confirming.authorized_notional == pytest.approx(2_500)
    assert conflicting.outcome == "authorized"
    assert conflicting.authorized_notional == pytest.approx(1_250)
    assert conflicting.notional_multiplier == 0.5


def test_active_stress_blocks_entries_but_never_blocks_hard_risk_exit():
    assessment = cross_assessment("stressed", 0, 0)
    blocked = RiskGate().evaluate(
        snapshot(), decision(), state(), position(), NOW,
        cross_venue=assessment, cross_venue_mode="active",
    )
    exit_gate = RiskGate().evaluate(
        snapshot(), decision(), state(), position(1, mark_price=98_900), NOW,
        cross_venue=assessment, cross_venue_mode="active",
    )

    assert blocked.outcome == "hold"
    assert "cross_venue_stressed" in blocked.reason_codes
    assert exit_gate.outcome == "reduce_only"
    assert exit_gate.reason_codes == ("stop_loss",)
