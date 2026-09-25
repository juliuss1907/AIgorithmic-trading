"""Safe, unified read model for the current portfolio worker dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from intraday.contracts import DecisionScope, ProviderRole
from intraday.portfolio_soak import (
    CURRENT_SOAK_EVIDENCE_VERSION,
    evaluate_portfolio_soak,
)
from intraday.portfolio_view import latest_parent_market_view


SOAK_DURATION = timedelta(hours=72)
HEARTBEAT_LIMITS = {
    DecisionScope.SPOT_DAILY.value: timedelta(hours=26),
    DecisionScope.PERP_INTRADAY.value: timedelta(hours=1),
}


def _choice(answers: dict[str, Any], name: str) -> str | None:
    value = answers.get(name)
    if not isinstance(value, dict):
        return None
    selected = value.get("choice")
    return selected if isinstance(selected, str) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def project_journal_signal(row: dict) -> dict:
    """Reduce an immutable journal row to explicitly public monitoring fields."""
    try:
        answers = json.loads(row["jev_answers"])
    except (json.JSONDecodeError, TypeError):
        answers = {}
    direction = _choice(answers, "direction")
    direction_answer = answers.get("direction", {})
    probabilities = (
        direction_answer.get("probabilities", {})
        if isinstance(direction_answer, dict)
        else {}
    )
    confidence = (
        _number(probabilities.get(direction))
        if isinstance(probabilities, dict) and direction is not None
        else None
    )
    if confidence is None and isinstance(direction_answer, dict):
        confidence = _number(direction_answer.get("confidence"))
    toxic_answer = answers.get("toxic_flow", {})
    toxic_flow = None
    if isinstance(toxic_answer, dict):
        toxic_flow = _number(
            toxic_answer.get("noul", toxic_answer.get("probability"))
        )
    quality_answer = answers.get("entry_quality", {})
    quality = (
        _number(quality_answer.get("score"))
        if isinstance(quality_answer, dict)
        else None
    )
    if quality is not None:
        quality += 1
    reason = row.get("gate_reason")
    if reason == "soak_observation_only":
        outcome = "OBSERVED"
    elif row.get("gate_passed"):
        outcome = "PASSED"
    else:
        outcome = "REJECTED"
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "symbol": row["symbol"],
        "scope": row["scope"],
        "market": row["market"],
        "direction": direction,
        "confidence": confidence,
        "regime": _choice(answers, "regime"),
        "toxic_flow": toxic_flow,
        "entry_quality": quality,
        "risk_level": _choice(answers, "risk_level"),
        "gate_passed": bool(row["gate_passed"]),
        "outcome": outcome,
        "gate_reason": reason,
        "rules_version": row["rules_version"],
        "decision_mode": row["decision_mode"],
    }


def list_public_signals(store, *, limit: int, scope: DecisionScope | None) -> list[dict]:
    return [
        project_journal_signal(row)
        for row in store.list_journal_signals(limit=limit, scope=scope)
    ]


def _public_call(call) -> dict:
    return {
        "workflow": call.workflow,
        "role": call.role.value,
        "profile_id": call.profile_id,
        "model": call.model,
        "status": call.status,
        "started_at": call.started_at.isoformat(),
        "completed_at": call.completed_at.isoformat(),
        "latency_ms": call.latency_ms,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cost_usd": call.cost_usd,
        "error_code": call.error_code,
    }


def _market_snapshot(store, *, market: str) -> dict | None:
    snapshot = store.latest_snapshot(market=market)
    if snapshot is None:
        return None
    reference = snapshot.features.get("reference_price")
    if reference is None:
        reference = (snapshot.bid + snapshot.ask) / 2
    return {
        "market": market,
        "timeframe": snapshot.timeframe,
        "reference_price": reference,
        "candle_close_price": snapshot.features.get(
            "candle_close_price", snapshot.features.get("price")
        ),
        "event_time": snapshot.event_time.isoformat(),
        "quality_flags": list(snapshot.quality_flags),
    }


def _provider_projection(store, calls: list) -> dict:
    profiles = {item["profile_id"]: item for item in store.list_provider_profiles()}
    latest_by_role = {}
    for call in calls:
        latest_by_role.setdefault(call.role.value, call)
    result = {}
    for role in ProviderRole:
        assignment = store.provider_assignment(role)
        profile = profiles.get(assignment["profile_id"]) if assignment else None
        call = latest_by_role.get(role.value)
        result[role.value] = {
            "active": assignment is not None,
            "profile_id": assignment["profile_id"] if assignment else None,
            "kind": profile["kind"] if profile else None,
            "model": profile["model"] if profile else None,
            "preflight_status": profile["last_test_status"] if profile else None,
            "latest_call": _public_call(call) if call else None,
        }
    return result


def build_dashboard_snapshot(store, *, now: datetime) -> dict:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("dashboard time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    started_at = store.first_model_backed_signal_at(feature_schema_version="2")
    all_ticks = store.list_portfolio_soak_ticks(
        evidence_version=CURRENT_SOAK_EVIDENCE_VERSION
    )
    ticks = []
    if started_at is not None:
        ticks = [
            item
            for item in all_ticks
            if datetime.fromisoformat(item["created_at"]) >= started_at
        ]
    live_evaluation = evaluate_portfolio_soak(
        ticks,
        evaluated_at=now,
        evidence_version=CURRENT_SOAK_EVIDENCE_VERSION,
    )
    latest_tick: dict[str, dict | None] = {
        scope.value: None for scope in DecisionScope
    }
    healthy_counts = {scope.value: 0 for scope in DecisionScope}
    for tick in ticks:
        latest_tick[tick["scope"]] = tick
        if tick["status"] in {"success", "skipped_no_setup"}:
            healthy_counts[tick["scope"]] += 1
    scopes = {}
    for scope in DecisionScope:
        name = scope.value
        item = latest_tick[name]
        latest_at = datetime.fromisoformat(item["created_at"]) if item else None
        age = (now - latest_at).total_seconds() if latest_at else None
        count = live_evaluation.sample_counts[name]
        scopes[name] = {
            "samples": count,
            "availability": live_evaluation.availability[name],
            "latest_status": item["status"] if item else None,
            "latest_at": latest_at.isoformat() if latest_at else None,
            "heartbeat_age_seconds": age,
            "healthy": bool(
                item
                and item["status"] in {"success", "skipped_no_setup"}
                and age is not None
                and age <= HEARTBEAT_LIMITS[name].total_seconds()
            ),
        }

    elapsed = max(timedelta(0), now - started_at) if started_at else timedelta(0)
    remaining = max(timedelta(0), SOAK_DURATION - elapsed)
    progress = min(100.0, elapsed / SOAK_DURATION * 100) if started_at else 0.0

    calls = store.list_model_calls(limit=20)
    providers = _provider_projection(store, calls)
    reasons = []
    if started_at is not None:
        for role in ProviderRole:
            provider = providers[role.value]
            if not provider["active"]:
                reasons.append(f"{role.value}_provider_inactive")
                continue
            call = provider["latest_call"]
            if call is None:
                reasons.append(f"{role.value}_call_missing")
                continue
            completed = datetime.fromisoformat(call["completed_at"])
            maximum_age = timedelta(seconds=90) if role == ProviderRole.JEV else timedelta(minutes=75)
            if call["status"] != "success":
                reasons.append(f"{role.value}_call_error")
            elif now - completed > maximum_age:
                reasons.append(f"{role.value}_call_stale")
        perp = scopes[DecisionScope.PERP_INTRADAY.value]
        if (
            not perp["healthy"]
            or perp["heartbeat_age_seconds"] is None
            or perp["heartbeat_age_seconds"] > 90
        ):
            reasons.append("perp_heartbeat_stale")

    parent = store.load_parent_portfolio_state()
    if parent is not None:
        parent = latest_parent_market_view(store, parent)
    if started_at is None:
        status_code = "WAITING"
        status_label = "Waiting for first model-backed signal"
    elif reasons:
        status_code = "DEGRADED"
        status_label = "Degraded"
    elif parent is not None and parent.paper_active:
        status_code = "PAPER_ACTIVE"
        status_label = "Paper active"
    else:
        status_code = "SOAK_ACTIVE"
        status_label = "Soak active"

    bundle = store.latest_market_thesis_bundle()
    counts = store.dashboard_counts()
    recent_signals = list_public_signals(store, limit=20, scope=None)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    persisted = store.latest_portfolio_soak_evaluation()
    return {
        "generated_at": now.isoformat(),
        "status": {
            "code": status_code,
            "label": status_label,
            "reasons": reasons,
        },
        "soak": {
            "evidence_version": CURRENT_SOAK_EVIDENCE_VERSION,
            "started_at": started_at.isoformat() if started_at else None,
            "target_at": (started_at + SOAK_DURATION).isoformat() if started_at else None,
            "elapsed_seconds": elapsed.total_seconds(),
            "remaining_seconds": remaining.total_seconds(),
            "progress_pct": progress,
            "preview": live_evaluation.model_dump(mode="json"),
            "persisted_evaluation": (
                persisted.model_dump(mode="json") if persisted else None
            ),
        },
        "counts": counts,
        "scopes": scopes,
        "signals": {"total": counts["signals"], "recent": recent_signals},
        "providers": providers,
        "recent_model_calls": [_public_call(call) for call in calls[:10]],
        "daily_model_cost_usd": store.model_cost_since(day_start),
        "markets": {
            "spot": _market_snapshot(store, market="binance_spot"),
            "perp": _market_snapshot(store, market="binance_usdm_perp"),
        },
        "portfolio": parent.model_dump(mode="json") if parent else None,
        "thesis": bundle.model_dump(mode="json") if bundle else None,
        "scheduler": store.latest_scheduler_runs(),
    }
