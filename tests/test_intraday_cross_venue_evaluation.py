from datetime import datetime, timezone

from intraday.cross_venue_evaluation import (
    CrossVenueEvaluationEvidence,
    evaluate_cross_venue_promotion,
)
from intraday.store import IntradayStore


NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def evidence(**updates):
    values = {
        "evaluation_id": "cross-eval-1",
        "evaluated_at": NOW,
        "collected_days": 14,
        "coverage": 0.96,
        "p95_book_skew_seconds": 1.5,
        "p95_metadata_age_seconds": 45,
        "duplicate_frames": 0,
        "checksum_mismatches": 0,
        "lookahead_violations": 0,
        "non_hold_decisions": 100,
        "closed_trades": 30,
        "baseline_return_pct": 2,
        "overlay_return_pct": 1.8,
        "baseline_max_drawdown_pct": 5,
        "overlay_max_drawdown_pct": 4.5,
        "baseline_expected_shortfall_pct": -1.5,
        "overlay_expected_shortfall_pct": -1.3,
        "hard_risk_violations": 0,
    }
    values.update(updates)
    return CrossVenueEvaluationEvidence(**values)


def test_promotion_gate_promotes_only_complete_non_inferior_evidence():
    result = evaluate_cross_venue_promotion(evidence())

    assert result.status == "promote"
    assert result.reasons == ()


def test_promotion_gate_defers_insufficient_samples_and_rejects_bad_risk():
    deferred = evaluate_cross_venue_promotion(evidence(collected_days=13, closed_trades=29))
    rejected = evaluate_cross_venue_promotion(
        evidence(overlay_max_drawdown_pct=5.1, hard_risk_violations=1)
    )

    assert deferred.status == "deferred"
    assert set(deferred.reasons) == {"minimum_days", "minimum_closed_trades"}
    assert rejected.status == "reject"
    assert set(rejected.reasons) == {"drawdown_worse", "hard_risk_violation"}


def test_latest_promotion_record_controls_active_mode(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    result = evaluate_cross_venue_promotion(evidence())

    store.record_cross_venue_evaluation(result)

    assert store.latest_cross_venue_evaluation().status == "promote"
    assert store.cross_venue_activation_allowed() is True
