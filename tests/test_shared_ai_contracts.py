from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from intraday.contracts import (
    DecisionScope,
    Direction,
    HorizonThesis,
    JevDecision,
    KeyLevels,
    MarketThesisBundle,
    PerpRuleParameters,
    Regime,
    RiskLevel,
    ScopedJevDecision,
    DecisionMode,
    StateVariant,
    ScopedRuleCandidate,
    SpotRuleParameters,
)


NOW = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)


def horizon(scope: DecisionScope) -> HorizonThesis:
    return HorizonThesis(
        scope=scope,
        summary="The supplied evidence supports a cautious directional posture.",
        stance="bullish",
        confidence=0.72,
        key_levels=KeyLevels(support=98_000, resistance=102_000),
        risk_factors=("Unexpected macro headline",),
        horizon_minutes=(240 if scope == DecisionScope.PERP_INTRADAY else 2_880),
    )


def test_market_thesis_bundle_requires_both_distinct_horizons():
    bundle = MarketThesisBundle(
        thesis_id="bundle-1",
        intraday=horizon(DecisionScope.PERP_INTRADAY),
        daily_swing=horizon(DecisionScope.SPOT_DAILY),
        source_report_ids=("market-1", "news-1", "sentiment-1"),
        generated_at=NOW,
        model_ref="llm-main@fingerprint",
        prompt_version="analysis-v2",
    )

    assert bundle.intraday.horizon_minutes == 240
    assert bundle.daily_swing.horizon_minutes == 2_880

    with pytest.raises(ValidationError, match="daily_swing"):
        MarketThesisBundle(
            thesis_id="bundle-invalid",
            intraday=horizon(DecisionScope.PERP_INTRADAY),
            daily_swing=horizon(DecisionScope.PERP_INTRADAY),
            source_report_ids=("market-1", "news-1", "sentiment-1"),
            generated_at=NOW,
            model_ref="llm-main@fingerprint",
            prompt_version="analysis-v2",
        )


def test_spot_rule_bounds_donchian_and_requires_exit_below_entry():
    rule = SpotRuleParameters(
        entry_window=55,
        exit_window=20,
        atr_period=14,
        jev_confidence_threshold=0.90,
    )

    assert rule.entry_window == 55
    assert rule.exit_window == 20

    with pytest.raises(ValidationError, match="exit_window"):
        SpotRuleParameters(entry_window=20, exit_window=20)
    with pytest.raises(ValidationError):
        SpotRuleParameters(entry_window=61, exit_window=20)


def test_scoped_rule_candidate_rejects_parameters_for_other_sleeve():
    spot = ScopedRuleCandidate.create(
        rule_id="spot-candidate-1",
        parent_rule_id="spot-champion-1",
        thesis_id="bundle-1",
        scope=DecisionScope.SPOT_DAILY,
        parameters=SpotRuleParameters(),
        created_at=NOW,
        model_ref="llm-main@fingerprint",
        prompt_version="analysis-v2",
        rationale="Keep the Donchian setup bounded while requiring Jev confirmation.",
    )

    assert spot.scope == DecisionScope.SPOT_DAILY
    assert len(spot.content_hash) == 64

    with pytest.raises(ValidationError, match="parameters"):
        ScopedRuleCandidate.create(
            rule_id="bad-candidate-1",
            parent_rule_id="spot-champion-1",
            thesis_id="bundle-1",
            scope=DecisionScope.SPOT_DAILY,
            parameters=PerpRuleParameters(),
            created_at=NOW,
            model_ref="llm-main@fingerprint",
            prompt_version="analysis-v2",
            rationale="This deliberately uses the wrong parameter contract.",
        )


def test_scoped_jev_decision_carries_workflow_without_changing_typed_decision():
    decision = JevDecision(
        decision_id="decision-1",
        tick_id="BTCUSDT:spot:1",
        snapshot_id="snapshot-1",
        direction=Direction.BUY,
        direction_confidence=0.91,
        regime=Regime.TRENDING_UP,
        toxic_flow=0.1,
        entry_quality=4,
        risk_level=RiskLevel.LOW,
        model_ref="jev-native@fingerprint",
        created_at=NOW,
    )
    scoped = ScopedJevDecision(
        scope=DecisionScope.SPOT_DAILY,
        workflow="spot_daily_entry",
        decision=decision,
    )

    assert scoped.decision.direction == Direction.BUY
    assert scoped.state_variant == StateVariant.NUMERIC_V1
    assert scoped.decision_mode == DecisionMode.PRIMARY

    with pytest.raises(ValidationError, match="workflow"):
        ScopedJevDecision(
            scope=DecisionScope.SPOT_DAILY,
            workflow="perp_intraday_entry",
            decision=decision,
        )
