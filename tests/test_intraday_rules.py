from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from intraday.contracts import Regime, RuleCandidate, RuleParameters
from intraday.rules import (
    Performance,
    ReplayEvidence,
    advance_rule_lifecycle,
    evaluate_promotion,
    evaluate_replay_gate,
)
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def candidate(**parameters):
    return RuleCandidate.create(
        rule_id="candidate-1",
        parent_rule_id="champion-1",
        thesis_id="thesis-1",
        parameters=RuleParameters(**parameters),
        created_at=NOW,
        model_ref="stub/analysis-v1",
        prompt_version="rules-v1",
    )


def test_candidate_is_content_addressed_and_cannot_modify_hard_risk():
    first = candidate(confidence_threshold=0.9, allowed_regimes=(Regime.TRENDING_UP,))
    second = candidate(confidence_threshold=0.9, allowed_regimes=(Regime.TRENDING_UP,))

    assert first.content_hash == second.content_hash
    with pytest.raises(ValidationError):
        RuleParameters(max_drawdown_pct=0.5)
    with pytest.raises(ValidationError):
        RuleParameters(confidence_threshold=0.2)


def test_promotion_defers_until_both_duration_and_trade_floor_are_met():
    result = evaluate_promotion(
        candidate_id="candidate-1",
        champion_id="champion-1",
        started_at=NOW - timedelta(days=13),
        evaluated_at=NOW,
        champion=Performance(net_return_pct=1.0, max_drawdown_pct=2.0, closed_trades=40),
        challenger=Performance(net_return_pct=1.5, max_drawdown_pct=2.0, closed_trades=29),
        coverage=1.0,
    )

    assert result.status == "deferred"
    assert set(result.reasons) == {"minimum_duration", "minimum_closed_trades"}


def test_promotion_requires_positive_risk_adjusted_outperformance_and_coverage():
    accepted = evaluate_promotion(
        candidate_id="candidate-1",
        champion_id="champion-1",
        started_at=NOW - timedelta(days=14),
        evaluated_at=NOW,
        champion=Performance(net_return_pct=1.0, max_drawdown_pct=2.0, closed_trades=35),
        challenger=Performance(net_return_pct=1.3, max_drawdown_pct=2.0, closed_trades=35),
        coverage=0.995,
    )
    rejected = evaluate_promotion(
        candidate_id="candidate-1",
        champion_id="champion-1",
        started_at=NOW - timedelta(days=14),
        evaluated_at=NOW,
        champion=Performance(net_return_pct=1.0, max_drawdown_pct=2.0, closed_trades=35),
        challenger=Performance(net_return_pct=1.05, max_drawdown_pct=2.0, closed_trades=35),
        coverage=0.98,
    )

    assert accepted.status == "promote"
    assert accepted.challenger_score == pytest.approx(0.65)
    assert rejected.status == "reject"
    assert "coverage_below_99pct" in rejected.reasons
    assert "insufficient_outperformance" in rejected.reasons


def test_replay_gate_defers_until_90_days_of_complete_evidence():
    result = evaluate_replay_gate(
        candidate_id="candidate-1",
        champion_id="champion-1",
        evaluated_at=NOW,
        evidence=ReplayEvidence(
            history_days=89.9,
            decision_coverage=0.98,
            feature_coverage=0.94,
            champion=Performance(2, 3, 40),
            challenger=Performance(3, 3, 40),
            champion_expected_shortfall_pct=-0.2,
            challenger_expected_shortfall_pct=-0.2,
        ),
    )

    assert result.status == "deferred"
    assert set(result.reasons) == {
        "minimum_90_day_history",
        "decision_coverage_below_99pct",
        "feature_coverage_below_95pct",
    }


def test_replay_gate_rejects_unsafe_candidate_and_passes_bounded_outperformance():
    unsafe = evaluate_replay_gate(
        candidate_id="candidate-1",
        champion_id="champion-1",
        evaluated_at=NOW,
        evidence=ReplayEvidence(
            history_days=90,
            decision_coverage=1,
            feature_coverage=1,
            champion=Performance(2, 3, 40),
            challenger=Performance(-1, 8, 40),
            champion_expected_shortfall_pct=-0.2,
            challenger_expected_shortfall_pct=-0.8,
        ),
    )
    safe = evaluate_replay_gate(
        candidate_id="candidate-2",
        champion_id="champion-1",
        evaluated_at=NOW,
        evidence=ReplayEvidence(
            history_days=90,
            decision_coverage=1,
            feature_coverage=1,
            champion=Performance(2, 3, 40),
            challenger=Performance(2.5, 2.8, 42),
            champion_expected_shortfall_pct=-0.2,
            challenger_expected_shortfall_pct=-0.18,
        ),
    )

    assert unsafe.status == "reject"
    assert "hard_risk_failure" in unsafe.reasons
    assert safe.status == "pass"


def test_lifecycle_only_moves_replay_passed_candidate_into_challenger(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    champion = candidate()
    champion = champion.model_copy(
        update={"rule_id": "champion-1", "parent_rule_id": "root"}
    )
    # Recompute through the public constructor after changing content-addressed fields.
    champion = RuleCandidate.create(
        rule_id="champion-1",
        parent_rule_id="root",
        thesis_id=champion.thesis_id,
        parameters=champion.parameters,
        created_at=NOW,
        model_ref=champion.model_ref,
        prompt_version=champion.prompt_version,
    )
    pending = candidate(confidence_threshold=0.9)
    store.register_rule(champion, status="champion")
    store.activate_champion(champion.rule_id, now=NOW)
    store.register_rule(pending, status="queued")

    result = advance_rule_lifecycle(
        store,
        now=NOW,
        replay_evaluator=lambda **kwargs: evaluate_replay_gate(
            candidate_id=pending.rule_id,
            champion_id=champion.rule_id,
            evaluated_at=NOW,
            evidence=ReplayEvidence(
                history_days=90,
                decision_coverage=1,
                feature_coverage=1,
                champion=Performance(1, 2, 30),
                challenger=Performance(1.5, 2, 30),
                champion_expected_shortfall_pct=-0.2,
                challenger_expected_shortfall_pct=-0.2,
            ),
        ),
    )

    assert result["status"] == "challenger_started"
    assert store.rule_status(pending.rule_id) == "challenger"
    assert store.rule_registry()["champion_id"] == champion.rule_id
