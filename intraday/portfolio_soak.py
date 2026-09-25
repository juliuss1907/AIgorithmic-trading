"""Evidence-based promotion gate for the combined decision-only paper soak."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from intraday.contracts import DecisionScope, FeatureSnapshot
from intraday.journal import record_scoped_signal
from intraday.spot_signal import evaluate_donchian


CURRENT_SOAK_EVIDENCE_VERSION = "scope-price-v2"
LEGACY_SOAK_EVIDENCE_VERSION = "market-v1"


class PortfolioSoakEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(min_length=16, max_length=64)
    evidence_version: str = Field(min_length=1, max_length=80)
    status: Literal["deferred", "reject", "pass"]
    started_at: datetime | None
    evaluated_at: datetime
    duration_hours: float = Field(ge=0)
    sample_counts: dict[str, int]
    availability: dict[str, float]
    hard_risk_violations: int = Field(ge=0)
    reason_codes: tuple[str, ...] = ()

    @field_validator("started_at", "evaluated_at")
    @classmethod
    def times_are_aware(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("soak timestamps must be timezone-aware")
        return value


def run_soak_cycle(
    store,
    provider,
    snapshot: FeatureSnapshot,
    *,
    now: datetime,
    scopes: tuple[DecisionScope, ...] = (
        DecisionScope.SPOT_DAILY,
        DecisionScope.PERP_INTRADAY,
    ),
) -> dict[str, str]:
    """Exercise both Jev workflows and persist health only; never mutate positions."""
    store.record_snapshot(snapshot)
    result = {}
    for scope in scopes:
        tick_id = f"{snapshot.symbol}:{scope.value}:{int(now.timestamp() * 1000)}"
        try:
            scoped = provider.decide_scoped(snapshot, tick_id, scope, now)
        except RuntimeError:
            status = "provider_error"
        else:
            rule = store.load_active_scoped_rule(scope)
            try:
                record_scoped_signal(
                    store,
                    snapshot,
                    scoped,
                    gate_passed=False,
                    gate_reason="soak_observation_only",
                    rule_id=(
                        rule.rule_id if rule is not None else "soak_no_active_rule"
                    ),
                )
            except (ValueError, sqlite3.Error):
                status = "gate_error"
            else:
                status = "success"
        store.record_portfolio_soak_tick(
            scope=scope,
            status=status,
            created_at=now,
        )
        result[scope.value] = status
    return result


def run_spot_soak_observation(
    store,
    provider,
    snapshot: FeatureSnapshot,
    *,
    now: datetime,
    rule,
    candles: list[list] | None,
) -> str:
    """Record the daily Spot heartbeat and call Jev only for a valid setup."""
    if rule is None or not candles:
        store.record_portfolio_soak_tick(
            scope=DecisionScope.SPOT_DAILY,
            status="skipped_no_setup",
            created_at=now,
        )
        return "skipped_no_setup"
    observation = evaluate_donchian(candles, rule.parameters)
    if not observation.entry:
        store.record_portfolio_soak_tick(
            scope=DecisionScope.SPOT_DAILY,
            status="skipped_no_setup",
            created_at=now,
        )
        return "skipped_no_setup"
    return run_soak_cycle(
        store,
        provider,
        snapshot,
        now=now,
        scopes=(DecisionScope.SPOT_DAILY,),
    )[DecisionScope.SPOT_DAILY.value]


def evaluate_portfolio_soak(
    records: list[dict],
    *,
    evaluated_at: datetime,
    evidence_version: str = CURRENT_SOAK_EVIDENCE_VERSION,
) -> PortfolioSoakEvaluation:
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluation time must be timezone-aware")
    ordered = sorted(
        (
            item
            for item in records
            if item.get("evidence_version", LEGACY_SOAK_EVIDENCE_VERSION)
            == evidence_version
        ),
        key=lambda item: item["created_at"],
    )
    started_at = (
        datetime.fromisoformat(ordered[0]["created_at"]) if ordered else None
    )
    duration = (
        max(0.0, (evaluated_at - started_at).total_seconds() / 3600)
        if started_at
        else 0.0
    )
    counts = {scope.value: 0 for scope in DecisionScope}
    healthy = {scope.value: 0 for scope in DecisionScope}
    latest = {scope.value: None for scope in DecisionScope}
    violations = 0
    good_statuses = {"success", "skipped_no_setup"}
    for item in ordered:
        scope = DecisionScope(item["scope"]).value
        counts[scope] += 1
        healthy[scope] += item["status"] in good_statuses
        created_at = datetime.fromisoformat(item["created_at"])
        latest[scope] = created_at
        violations += bool(item.get("hard_risk_violation", False))
    availability = {
        scope: healthy[scope] / counts[scope] if counts[scope] else 0.0
        for scope in counts
    }
    waiting = []
    if duration < 72:
        waiting.append("minimum_72_hours")
    minimum_samples = {
        DecisionScope.SPOT_DAILY.value: 3,
        DecisionScope.PERP_INTRADAY.value: 100,
    }
    maximum_heartbeat_age = {
        DecisionScope.SPOT_DAILY.value: 26 * 3600,
        DecisionScope.PERP_INTRADAY.value: 3600,
    }
    for scope, minimum in minimum_samples.items():
        if counts[scope] < minimum:
            waiting.append(f"{scope}_minimum_samples")
        if (
            latest[scope] is None
            or (evaluated_at - latest[scope]).total_seconds()
            > maximum_heartbeat_age[scope]
        ):
            waiting.append(f"{scope}_recent_heartbeat_missing")
    failures = []
    if violations:
        failures.append("hard_risk_violation")
    for scope in counts:
        if counts[scope] and availability[scope] < 0.95:
            failures.append(f"{scope}_availability_below_95pct")
    if waiting:
        status = "deferred"
        reasons = tuple(waiting)
    elif failures:
        status = "reject"
        reasons = tuple(failures)
    else:
        status = "pass"
        reasons = ()
    identity = hashlib.sha256(
        (
            f"portfolio-soak:{evidence_version}:{started_at}:{evaluated_at.isoformat()}:"
            f"{counts}:{healthy}:{violations}:{status}"
        ).encode()
    ).hexdigest()[:32]
    return PortfolioSoakEvaluation(
        evaluation_id=identity,
        evidence_version=evidence_version,
        status=status,
        started_at=started_at,
        evaluated_at=evaluated_at,
        duration_hours=duration,
        sample_counts=counts,
        availability=availability,
        hard_risk_violations=violations,
        reason_codes=reasons,
    )
