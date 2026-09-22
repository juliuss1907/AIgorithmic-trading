from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from intraday.contracts import Regime, RuleCandidate, RuleParameters
from intraday.rules import Performance, evaluate_promotion


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
