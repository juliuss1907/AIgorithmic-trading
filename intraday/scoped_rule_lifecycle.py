"""Manual, evidence-gated lifecycle for scoped JSON trading rules."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from intraday.contracts import DecisionScope, Direction, Regime, RiskLevel


class ScopedRuleEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(min_length=16, max_length=64)
    candidate_id: str
    scope: DecisionScope
    kind: Literal["replay", "soak"]
    status: Literal["deferred", "reject", "pass"]
    evaluated_at: datetime
    started_at: datetime | None = None
    sample_count: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    champion_score: float
    challenger_score: float
    reason_codes: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values):
        identity = hashlib.sha256(
            json.dumps(values, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()[:32]
        return cls(evaluation_id=identity, **values)


def rule_allows_answers(rule, answers: dict) -> bool:
    direction = Direction(answers["direction"]["choice"])
    confidence = float(answers["direction"]["probabilities"].get(direction.value, 0))
    if direction in {Direction.HOLD, Direction.TAKE_PROFIT}:
        return False
    regime = Regime(answers["regime"]["choice"])
    risk = RiskLevel(answers["risk_level"]["choice"])
    toxic = float(answers["toxic_flow"]["noul"])
    if rule.scope == DecisionScope.SPOT_DAILY:
        return (
            direction in {Direction.BUY, Direction.STRONG_BUY}
            and confidence >= rule.parameters.jev_confidence_threshold
            and regime in rule.parameters.allowed_regimes
            and risk not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            and toxic <= 0.30
        )
    quality = float(answers["entry_quality"]["score"]) + 1
    return (
        confidence >= rule.parameters.confidence_threshold
        and quality >= rule.parameters.entry_quality_min
        and toxic <= rule.parameters.toxic_flow_max
        and regime in rule.parameters.allowed_regimes
        and risk not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    )


def _score(rule, rows: list[dict]) -> tuple[float, int]:
    returns = []
    for row in rows:
        try:
            allowed = rule_allows_answers(rule, json.loads(row["jev_answers"]))
        except (KeyError, TypeError, ValueError):
            continue
        if allowed:
            returns.append(float(row["directional_return_pct"] or 0))
    return (fmean(returns) if returns else 0.0, len(returns))


def evaluate_scoped_replay(store, candidate_id: str, *, now: datetime) -> ScopedRuleEvaluation:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None:
        raise ValueError("unknown scoped rule candidate")
    champion = store.load_active_scoped_rule(candidate.scope)
    if champion is None or candidate.parent_rule_id != champion.rule_id:
        raise ValueError("candidate does not descend from the active champion")
    rows, summary = store.scoped_rule_replay_evidence(candidate.scope)
    champion_score, champion_count = _score(champion, rows)
    challenger_score, challenger_count = _score(candidate, rows)
    reasons = []
    if summary["outcomes"] < 100:
        reasons.append("minimum_100_outcomes")
    if summary["history_days"] < 14:
        reasons.append("minimum_14_day_history")
    if summary["coverage"] < 0.95:
        reasons.append("outcome_coverage_below_95pct")
    if reasons:
        status = "deferred"
    elif challenger_count == 0:
        status, reasons = "reject", ["candidate_authorizes_no_samples"]
    elif challenger_score < champion_score - 0.05:
        status, reasons = "reject", ["replay_underperformance"]
    else:
        status, reasons = "pass", []
    evaluation = ScopedRuleEvaluation.create(
        candidate_id=candidate.rule_id, scope=candidate.scope, kind="replay",
        status=status, evaluated_at=now, started_at=None,
        sample_count=min(champion_count, challenger_count),
        coverage=summary["coverage"], champion_score=champion_score,
        challenger_score=challenger_score, reason_codes=tuple(reasons),
    )
    store.record_scoped_rule_evaluation(evaluation)
    if status == "pass":
        store.update_scoped_rule_status(
            candidate.rule_id, expected="queued", status="replay_passed"
        )
    elif status == "reject":
        store.update_scoped_rule_status(
            candidate.rule_id, expected="queued", status="rejected"
        )
    return evaluation


def start_scoped_rule_soak(store, candidate_id: str, *, now: datetime) -> dict:
    evaluation = store.latest_scoped_rule_evaluation(candidate_id, kind="replay")
    if evaluation is None or evaluation.status != "pass":
        raise ValueError("candidate requires a passing replay evaluation")
    if store.scoped_rule_status(candidate_id) == "queued":
        store.update_scoped_rule_status(
            candidate_id, expected="queued", status="replay_passed"
        )
    return store.set_scoped_challenger(candidate_id, now=now)


def evaluate_scoped_soak(store, candidate_id: str, *, now: datetime) -> ScopedRuleEvaluation:
    candidate = store.load_scoped_rule(candidate_id)
    registry = store.scoped_rule_registry(candidate.scope) if candidate else {}
    if candidate is None or registry.get("challenger_id") != candidate_id:
        raise ValueError("candidate is not the active scoped challenger")
    started_at = datetime.fromisoformat(registry["updated_at"])
    rows = store.list_scoped_rule_soak_ticks(candidate_id)
    interval = 86_400 if candidate.scope == DecisionScope.SPOT_DAILY else 30
    expected = max(1, int((now - started_at).total_seconds() / interval))
    coverage = min(1.0, len(rows) / expected)
    completed = [row for row in rows if row["directional_return_pct"] is not None]
    champion_score = fmean(
        float(row["directional_return_pct"]) if row["champion_allowed"] else 0
        for row in completed
    ) if completed else 0
    challenger_score = fmean(
        float(row["directional_return_pct"]) if row["challenger_allowed"] else 0
        for row in completed
    ) if completed else 0
    reasons = []
    if now - started_at < timedelta(hours=72):
        reasons.append("minimum_72_hours")
    minimum = 3 if candidate.scope == DecisionScope.SPOT_DAILY else 100
    if len(completed) < minimum:
        reasons.append("minimum_soak_samples")
    if coverage < 0.95:
        reasons.append("soak_coverage_below_95pct")
    if reasons:
        status = "deferred"
    elif challenger_score < champion_score - 0.05:
        status, reasons = "reject", ["soak_underperformance"]
    else:
        status, reasons = "pass", []
    evaluation = ScopedRuleEvaluation.create(
        candidate_id=candidate_id, scope=candidate.scope, kind="soak",
        status=status, evaluated_at=now, started_at=started_at,
        sample_count=len(completed), coverage=coverage,
        champion_score=champion_score, challenger_score=challenger_score,
        reason_codes=tuple(reasons),
    )
    store.record_scoped_rule_evaluation(evaluation)
    return evaluation


def activate_scoped_rule(
    store, candidate_id: str, *, evaluation_id: str, now: datetime
) -> dict:
    evaluation = store.scoped_rule_evaluation(evaluation_id)
    if (
        evaluation is None or evaluation.candidate_id != candidate_id
        or evaluation.kind != "soak" or evaluation.status != "pass"
    ):
        raise ValueError("activation requires the exact passing soak evaluation")
    return store.promote_scoped_challenger(candidate_id, now=now)
