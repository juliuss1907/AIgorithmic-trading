"""Decision-first Perp bootstrap with pre/post-replay evidence separation."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import fmean
import json

from intraday.assets import asset_spec
from intraday.contracts import DecisionScope, PerpRuleParameters, ScopedRuleCandidate
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation, rule_allows_answers
from intraday.rule_preview import RuleGatePreview


SCOPE = DecisionScope.PERP_INTRADAY
PERP_ROUND_TRIP_COST_PCT = 0.10


def bootstrap_perp_rule(store, symbol: str, *, now: datetime) -> ScopedRuleCandidate:
    symbol = store.asset_spec(symbol).symbol
    if store.load_active_scoped_rule(SCOPE, symbol=symbol):
        raise ValueError("asset already has a Perp champion")
    if store.has_open_scoped_rule_candidate(SCOPE, symbol=symbol):
        raise ValueError("asset already has an open Perp candidate")
    candidate = ScopedRuleCandidate.create(
        rule_id=f"{symbol.lower()}-perp-baseline-v1",
        parent_rule_id="bootstrap", thesis_id="deterministic-baseline",
        scope=SCOPE, symbol=symbol, parameters=PerpRuleParameters(),
        created_at=now, model_ref="deterministic/baseline",
        prompt_version="perp-baseline-v1",
        rationale="Bounded Perp decision baseline; collect model evidence before replay.",
    )
    store.register_scoped_rule(candidate)
    return candidate


def start_perp_decision_soak(store, candidate_id: str, *, now: datetime) -> dict:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None or candidate.scope is not SCOPE:
        raise ValueError("unknown Perp candidate")
    lifecycle = store.asset_lifecycle(candidate.symbol, SCOPE).start_soak()
    registry = store.start_perp_decision_challenger(candidate_id, now=now)
    store.save_asset_lifecycle(lifecycle, updated_at=now)
    return registry


def _eligible_returns(candidate: ScopedRuleCandidate, rows: list[dict]) -> list[float]:
    returns = []
    for row in rows:
        try:
            if rule_allows_answers(candidate, json.loads(row["jev_answers"])):
                value = row["directional_return_pct"]
                if value is not None:
                    returns.append(float(value) - PERP_ROUND_TRIP_COST_PCT)
        except (KeyError, TypeError, ValueError):
            continue
    return returns


def preview_perp_bootstrap(store, candidate_id: str, *, now: datetime) -> RuleGatePreview:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None or candidate.scope is not SCOPE:
        raise ValueError("unknown Perp candidate")
    registry = store.scoped_rule_registry(SCOPE, symbol=candidate.symbol)
    if registry["challenger_id"] != candidate_id:
        raise ValueError("candidate is not the active Perp decision-soak challenger")
    started = datetime.fromisoformat(registry["updated_at"])
    rows, summary = store.scoped_rule_replay_evidence(
        SCOPE, symbol=candidate.symbol, after=started, before=now,
    )
    health = store.asset_soak_heartbeat_health(
        candidate.symbol, SCOPE, started_at=started, evaluated_at=now,
        interval_seconds=30,
    )
    scores = _eligible_returns(candidate, rows)
    waiting = []
    if now - started < timedelta(days=14):
        waiting.append("minimum_14_days")
    if summary["outcomes"] < 100:
        waiting.append("minimum_100_matured_15m_outcomes")
    if summary["coverage"] < .95:
        waiting.append("outcome_coverage_below_95pct")
    if health["coverage"] < .95:
        waiting.append("decision_heartbeat_coverage_below_95pct")
    rejected = []
    if health["hard_risk_violations"]:
        rejected.append("hard_risk_violation")
    if not scores:
        rejected.append("candidate_authorizes_no_samples")
    elif fmean(scores) <= 0:
        rejected.append("nonpositive_after_cost_score")
    status = "reject" if "hard_risk_violation" in rejected else (
        "deferred" if waiting else "reject" if rejected else "pass"
    )
    values = dict(
        candidate_id=candidate_id, symbol=candidate.symbol, scope=SCOPE,
        kind="replay", status=status, evaluated_at=now, started_at=started,
        sample_count=len(scores), coverage=summary["coverage"],
        champion_score=0, challenger_score=fmean(scores) if scores else 0,
        reason_codes=tuple(rejected + waiting if status == "reject" else waiting),
        metrics={"outcomes": summary["outcomes"], "signals": summary["signals"],
                 "history_days": summary["history_days"],
                 "heartbeat_coverage": health["coverage"]},
    )
    return RuleGatePreview(values, tuple(rejected + waiting), health["hard_risk_violations"])


def replay_perp_bootstrap(store, candidate_id: str, *, now: datetime) -> ScopedRuleEvaluation:
    evaluation = ScopedRuleEvaluation.create(**preview_perp_bootstrap(store, candidate_id, now=now).values)
    store.record_scoped_rule_evaluation(evaluation)
    if evaluation.status == "reject":
        store.reject_scoped_challenger(candidate_id, now=now)
    return evaluation


def preview_perp_post_replay(
    store, candidate_id: str, *, now: datetime,
) -> RuleGatePreview:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None or candidate.scope is not SCOPE:
        raise ValueError("unknown Perp candidate")
    registry = store.scoped_rule_registry(SCOPE, symbol=candidate.symbol)
    if registry["challenger_id"] != candidate_id:
        raise ValueError("candidate is not the active Perp challenger")
    replay = store.latest_scoped_rule_evaluation(candidate_id, kind="replay")
    started = replay.evaluated_at if replay and replay.status == "pass" else now
    rows, summary = store.scoped_rule_replay_evidence(
        SCOPE, symbol=candidate.symbol, after=started, before=now,
    )
    health = store.asset_soak_heartbeat_health(
        candidate.symbol, SCOPE, started_at=started, evaluated_at=now,
        interval_seconds=30,
    )
    scores = _eligible_returns(candidate, rows)
    waiting = []
    if replay is None or replay.status != "pass":
        waiting.append("passing_pre_cutoff_replay_required")
    if now - started < timedelta(hours=72):
        waiting.append("minimum_72_hours_post_replay")
    if summary["outcomes"] < 100:
        waiting.append("minimum_100_post_replay_outcomes")
    if summary["coverage"] < .95:
        waiting.append("post_replay_coverage_below_95pct")
    if health["coverage"] < .95:
        waiting.append("post_replay_heartbeat_coverage_below_95pct")
    rejected = []
    if summary["outcomes"] >= 100 and (not scores or fmean(scores) <= 0):
        rejected.append("nonpositive_post_replay_score")
    if health["hard_risk_violations"]:
        rejected.append("hard_risk_violation")
    status = "reject" if "hard_risk_violation" in rejected else (
        "deferred" if waiting else "reject" if rejected else "pass"
    )
    values = dict(
        candidate_id=candidate_id, symbol=candidate.symbol, scope=SCOPE,
        kind="soak", status=status, evaluated_at=now, started_at=started,
        sample_count=len(scores), coverage=summary["coverage"],
        champion_score=0, challenger_score=fmean(scores) if scores else 0,
        reason_codes=tuple(rejected + waiting if status == "reject" else waiting),
        metrics={"outcomes": summary["outcomes"], "signals": summary["signals"],
                 "heartbeat_coverage": health["coverage"]},
    )
    return RuleGatePreview(values, tuple(rejected + waiting), health["hard_risk_violations"])


def evaluate_perp_post_replay(store, candidate_id: str, *, now: datetime) -> ScopedRuleEvaluation:
    evaluation = ScopedRuleEvaluation.create(**preview_perp_post_replay(store, candidate_id, now=now).values)
    store.record_scoped_rule_evaluation(evaluation)
    if evaluation.status == "reject":
        store.reject_scoped_challenger(candidate_id, now=now)
    return evaluation


def activate_perp_bootstrap(
    store, candidate_id: str, *, evaluation_id: str, now: datetime,
) -> dict:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None or candidate.scope is not SCOPE:
        raise ValueError("unknown Perp candidate")
    evaluation = store.scoped_rule_evaluation(evaluation_id)
    latest = store.latest_scoped_rule_evaluation(candidate_id, kind="soak")
    if (evaluation is None or latest is None or latest.evaluation_id != evaluation_id
            or evaluation.candidate_id != candidate_id or evaluation.status != "pass"
            or evaluation.kind != "soak"):
        raise ValueError("activation requires exact passing post-replay evaluation")
    registry = store.promote_scoped_challenger(candidate_id, now=now)
    store.save_asset_lifecycle(
        store.asset_lifecycle(candidate.symbol, SCOPE), updated_at=now,
        evaluation_id=evaluation_id,
    )
    return registry
