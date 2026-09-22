"""Pre-registered promotion gate for the cross-venue overlay."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from intraday.contracts import StrictContract, _aware


class CrossVenueEvaluationEvidence(StrictContract):
    evaluation_id: str = Field(min_length=4, max_length=128)
    evaluated_at: datetime
    collected_days: float = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    p95_book_skew_seconds: float = Field(ge=0)
    p95_metadata_age_seconds: float = Field(ge=0)
    duplicate_frames: int = Field(ge=0)
    checksum_mismatches: int = Field(ge=0)
    lookahead_violations: int = Field(ge=0)
    non_hold_decisions: int = Field(ge=0)
    closed_trades: int = Field(ge=0)
    baseline_return_pct: float
    overlay_return_pct: float
    baseline_max_drawdown_pct: float = Field(ge=0)
    overlay_max_drawdown_pct: float = Field(ge=0)
    baseline_expected_shortfall_pct: float
    overlay_expected_shortfall_pct: float
    hard_risk_violations: int = Field(ge=0)

    _evaluation_time_is_aware = field_validator("evaluated_at")(_aware)


class CrossVenuePromotionEvaluation(StrictContract):
    evaluation_id: str
    evaluated_at: datetime
    status: Literal["deferred", "reject", "promote"]
    reasons: tuple[str, ...]
    evidence: CrossVenueEvaluationEvidence

    _evaluation_time_is_aware = field_validator("evaluated_at")(_aware)


def evaluate_cross_venue_promotion(
    evidence: CrossVenueEvaluationEvidence,
) -> CrossVenuePromotionEvaluation:
    deferred = []
    if evidence.collected_days < 14:
        deferred.append("minimum_days")
    if evidence.coverage < 0.95:
        deferred.append("coverage_below_95")
    if evidence.p95_book_skew_seconds > 2:
        deferred.append("book_skew_high")
    if evidence.p95_metadata_age_seconds > 60:
        deferred.append("metadata_age_high")
    if evidence.non_hold_decisions < 100:
        deferred.append("minimum_non_hold_decisions")
    if evidence.closed_trades < 30:
        deferred.append("minimum_closed_trades")

    rejected = []
    if evidence.duplicate_frames:
        rejected.append("duplicate_frames")
    if evidence.checksum_mismatches:
        rejected.append("checksum_mismatch")
    if evidence.lookahead_violations:
        rejected.append("lookahead_violation")
    if evidence.overlay_max_drawdown_pct > evidence.baseline_max_drawdown_pct:
        rejected.append("drawdown_worse")
    if evidence.overlay_return_pct < evidence.baseline_return_pct - 0.25:
        rejected.append("return_non_inferiority_failed")
    if evidence.overlay_expected_shortfall_pct < evidence.baseline_expected_shortfall_pct:
        rejected.append("expected_shortfall_worse")
    if evidence.hard_risk_violations:
        rejected.append("hard_risk_violation")

    status = "deferred" if deferred else ("reject" if rejected else "promote")
    reasons = tuple(deferred if deferred else rejected)
    return CrossVenuePromotionEvaluation(
        evaluation_id=evidence.evaluation_id,
        evaluated_at=evidence.evaluated_at,
        status=status,
        reasons=reasons,
        evidence=evidence,
    )
