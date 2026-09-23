"""Deterministic numeric/compact evaluation and daily error retrospectives."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from intraday.contracts import DecisionScope, Direction


EVALUATION_HORIZONS = {
    DecisionScope.PERP_INTRADAY: 900,
    DecisionScope.SPOT_DAILY: 259_200,
}
DEADBAND_PCT = {
    DecisionScope.PERP_INTRADAY: 0.20,
    DecisionScope.SPOT_DAILY: 0.30,
}


class ExperimentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_pairs: int = Field(ge=0)
    paired_samples: int = Field(ge=0)
    duration_days: float = Field(ge=0)
    availability: float = Field(ge=0, le=1)
    direction_agreement: float = Field(ge=0, le=1)
    actionable_flip_rate: float = Field(ge=0, le=1)
    numeric_accuracy: float = Field(ge=0, le=1)
    compact_accuracy: float = Field(ge=0, le=1)
    numeric_brier: float = Field(ge=0)
    compact_brier: float = Field(ge=0)
    numeric_ece: float = Field(default=0, ge=0, le=1)
    compact_ece: float = Field(default=0, ge=0, le=1)
    numeric_latency_ms: float | None = Field(default=None, ge=0)
    compact_latency_ms: float | None = Field(default=None, ge=0)
    numeric_input_tokens: float | None = Field(default=None, ge=0)
    compact_input_tokens: float | None = Field(default=None, ge=0)
    numeric_cost_usd: float | None = Field(default=None, ge=0)
    compact_cost_usd: float | None = Field(default=None, ge=0)
    numeric_error_rate: float | None = Field(default=None, ge=0, le=1)
    compact_error_rate: float | None = Field(default=None, ge=0, le=1)
    token_reduction: float | None = None


class DecisionExperimentEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(min_length=16, max_length=64)
    scope: DecisionScope
    horizon_sec: int
    status: Literal["collecting", "eligible", "reject"]
    evaluated_at: datetime
    metrics: ExperimentMetrics
    reason_codes: tuple[str, ...] = ()


def _bucket(direction: str) -> str:
    if direction in {Direction.BUY.value, Direction.STRONG_BUY.value}:
        return "long"
    if direction in {Direction.SELL.value, Direction.STRONG_SELL.value}:
        return "short"
    return "hold"


def _truth(return_pct: float, deadband: float) -> str:
    return "long" if return_pct > deadband else "short" if return_pct < -deadband else "hold"


def _probabilities(answers_json: str) -> dict[str, float]:
    answer = json.loads(answers_json)["direction"]
    probabilities = answer.get("probabilities") or {}
    result = {"long": 0.0, "hold": 0.0, "short": 0.0}
    for direction, probability in probabilities.items():
        if isinstance(probability, (int, float)) and not isinstance(probability, bool):
            result[_bucket(direction)] += float(probability)
    total = sum(result.values())
    if total <= 0:
        result[_bucket(answer["choice"])] = 1.0
        total = 1.0
    return {key: value / total for key, value in result.items()}


def _brier(probabilities: dict[str, float], truth: str) -> float:
    return sum((probabilities[key] - (1 if key == truth else 0)) ** 2 for key in probabilities)


def _ece(samples: list[tuple[dict[str, float], str]]) -> float:
    if not samples:
        return 0.0
    bins = [[] for _ in range(10)]
    for probabilities, truth in samples:
        choice = max(probabilities, key=probabilities.get)
        confidence = probabilities[choice]
        bins[min(9, int(confidence * 10))].append((confidence, choice == truth))
    return sum(
        len(bucket) / len(samples)
        * abs(fmean(item[0] for item in bucket) - fmean(item[1] for item in bucket))
        for bucket in bins if bucket
    )


def evaluate_eligibility(metrics: ExperimentMetrics) -> tuple[str, tuple[str, ...]]:
    waiting = []
    if metrics.duration_days < 14:
        waiting.append("minimum_14_days")
    if metrics.primary_pairs < 1_000 or metrics.paired_samples < 1_000:
        waiting.append("minimum_1000_pairs")
    if metrics.availability < 0.95:
        waiting.append("availability_below_95pct")
    if metrics.token_reduction is None:
        waiting.append("token_metrics_missing")
    if waiting:
        return "collecting", tuple(waiting)
    failures = []
    if metrics.compact_accuracy < metrics.numeric_accuracy - 0.02:
        failures.append("accuracy_regression_above_2pp")
    if metrics.compact_brier > metrics.numeric_brier + 0.02:
        failures.append("brier_regression_above_0_02")
    if metrics.token_reduction < 0.15:
        failures.append("token_reduction_below_15pct")
    return ("reject", tuple(failures)) if failures else ("eligible", ())


def _telemetry(store, scope: DecisionScope) -> dict:
    prefix = "spot_daily_entry" if scope == DecisionScope.SPOT_DAILY else "perp_intraday_entry"
    calls = [call for call in store.list_model_calls(limit=1000) if call.workflow.startswith(prefix)]
    result = {}
    for name, compact in (("numeric", False), ("compact", True)):
        selected = [call for call in calls if call.workflow.endswith("_compact") is compact]
        successful = [call for call in selected if call.status == "success"]
        result[f"{name}_latency_ms"] = fmean(call.latency_ms for call in successful) if successful else None
        with_tokens = [call.input_tokens for call in successful if call.input_tokens is not None]
        result[f"{name}_input_tokens"] = fmean(with_tokens) if with_tokens else None
        costs = [call.cost_usd for call in successful if call.cost_usd is not None]
        result[f"{name}_cost_usd"] = sum(costs) if costs else None
        result[f"{name}_error_rate"] = (
            sum(call.status == "error" for call in selected) / len(selected)
            if selected else None
        )
    numeric = result["numeric_input_tokens"]
    compact = result["compact_input_tokens"]
    result["token_reduction"] = (
        1 - compact / numeric if numeric and compact is not None else None
    )
    return result


def evaluate_compact_experiment(
    store, *, scope: DecisionScope, evaluated_at: datetime
) -> DecisionExperimentEvaluation:
    horizon = EVALUATION_HORIZONS[scope]
    rows = store.list_decision_experiment_pairs(scope=scope, horizon_sec=horizon)
    summary = store.decision_experiment_pair_summary(scope)
    deadband = DEADBAND_PCT[scope]
    agreement = []
    flips = []
    numeric_correct = []
    compact_correct = []
    numeric_brier = []
    compact_brier = []
    numeric_calibration = []
    compact_calibration = []
    for row in rows:
        truth = _truth(float(row["forward_return_pct"]), deadband)
        numeric_choice = _bucket(row["numeric_direction"])
        compact_choice = _bucket(row["compact_direction"])
        agreement.append(numeric_choice == compact_choice)
        flips.append((numeric_choice == "hold") != (compact_choice == "hold"))
        numeric_correct.append(numeric_choice == truth)
        compact_correct.append(compact_choice == truth)
        numeric_probs = _probabilities(row["numeric_answers"])
        compact_probs = _probabilities(row["compact_answers"])
        numeric_brier.append(_brier(numeric_probs, truth))
        compact_brier.append(_brier(compact_probs, truth))
        numeric_calibration.append((numeric_probs, truth))
        compact_calibration.append((compact_probs, truth))

    def mean(values):
        return fmean(values) if values else 0.0

    metrics = ExperimentMetrics(
        primary_pairs=summary["primary_pairs"],
        paired_samples=len(rows),
        duration_days=summary["duration_days"],
        availability=summary["availability"],
        direction_agreement=mean(agreement),
        actionable_flip_rate=mean(flips),
        numeric_accuracy=mean(numeric_correct),
        compact_accuracy=mean(compact_correct),
        numeric_brier=mean(numeric_brier),
        compact_brier=mean(compact_brier),
        numeric_ece=_ece(numeric_calibration),
        compact_ece=_ece(compact_calibration),
        **_telemetry(store, scope),
    )
    status, reasons = evaluate_eligibility(metrics)
    identity = hashlib.sha256(
        f"{scope.value}:{evaluated_at.isoformat()}:{metrics.model_dump_json()}".encode()
    ).hexdigest()[:32]
    evaluation = DecisionExperimentEvaluation(
        evaluation_id=identity, scope=scope, horizon_sec=horizon,
        status=status, evaluated_at=evaluated_at, metrics=metrics,
        reason_codes=reasons,
    )
    store.record_decision_experiment_evaluation(evaluation)
    return evaluation


def generate_retrospective(
    store, *, report_date: date, generated_at: datetime
) -> dict:
    scopes = {}
    for scope in DecisionScope:
        horizon = EVALUATION_HORIZONS[scope]
        deadband = DEADBAND_PCT[scope]
        examples = []
        for row in store.list_signal_outcome_rows(
            scope=scope, horizon_sec=horizon, report_date=report_date
        ):
            answer = json.loads(row["jev_answers"])["direction"]
            direction = answer["choice"]
            confidence = float((answer.get("probabilities") or {}).get(direction, 0))
            forward = float(row["forward_return_pct"])
            category = None
            if _bucket(direction) != "hold" and confidence >= 0.85:
                signed = forward * (1 if _bucket(direction) == "long" else -1)
                if signed < -deadband:
                    category = "high_confidence_wrong"
            if category is None and not row["gate_passed"] and abs(forward) > deadband:
                category = "rejected_opportunity"
            if _bucket(direction) == "hold" and abs(forward) > deadband:
                category = "hold_large_move"
            if category is None:
                continue
            timestamp = datetime.fromisoformat(row["timestamp"])
            bucket = timestamp.replace(
                minute=(timestamp.minute // 15) * 15, second=0, microsecond=0
            )
            examples.append({
                "signal_id": row["id"], "category": category,
                "bucket": bucket.isoformat(), "direction": direction,
                "confidence": confidence, "forward_return_pct": forward,
            })
        scopes[scope.value] = {
            "examples": examples[:50],
            "total_candidates": len(examples),
            "truncated": len(examples) > 50,
        }
        for pair in store.list_decision_experiment_pairs(
            scope=scope, horizon_sec=horizon
        ):
            if datetime.fromisoformat(pair["timestamp"]).date() != report_date:
                continue
            if _bucket(pair["numeric_direction"]) == _bucket(pair["compact_direction"]):
                continue
            scopes[scope.value]["total_candidates"] += 1
            if len(scopes[scope.value]["examples"]) < 50:
                scopes[scope.value]["examples"].append({
                    "experiment_pair_id": pair["experiment_pair_id"],
                    "category": "numeric_compact_disagreement",
                    "direction": pair["numeric_direction"],
                    "compact_direction": pair["compact_direction"],
                    "forward_return_pct": pair["forward_return_pct"],
                })
            else:
                scopes[scope.value]["truncated"] = True
    content = {
        "report_date": report_date.isoformat(),
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "scopes": scopes,
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"))
    report_id = hashlib.sha256(encoded.encode()).hexdigest()[:32]
    report = {"report_id": report_id, "content_hash": hashlib.sha256(encoded.encode()).hexdigest(), **content}
    store.record_retrospective(report)
    return report
