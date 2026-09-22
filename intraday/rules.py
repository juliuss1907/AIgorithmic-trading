"""Bounded rule-candidate evaluation; no generated code is ever executed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import tempfile

from intraday.contracts import PromotionEvaluation, RuleReplayEvaluation


@dataclass(frozen=True)
class Performance:
    net_return_pct: float
    max_drawdown_pct: float
    closed_trades: int
    hard_risk_violations: int = 0

    @property
    def score(self) -> float:
        return self.net_return_pct / max(self.max_drawdown_pct, 0.5)


@dataclass(frozen=True)
class ReplayEvidence:
    history_days: float
    decision_coverage: float
    feature_coverage: float
    champion: Performance
    challenger: Performance
    champion_expected_shortfall_pct: float
    challenger_expected_shortfall_pct: float


def evaluate_replay_gate(
    *,
    candidate_id: str,
    champion_id: str,
    evaluated_at: datetime,
    evidence: ReplayEvidence,
) -> RuleReplayEvaluation:
    waiting = []
    if evidence.history_days < 90:
        waiting.append("minimum_90_day_history")
    if evidence.decision_coverage < 0.99:
        waiting.append("decision_coverage_below_99pct")
    if evidence.feature_coverage < 0.95:
        waiting.append("feature_coverage_below_95pct")
    if waiting:
        status = "deferred"
        reasons = tuple(waiting)
    else:
        failures = []
        challenger = evidence.challenger
        if challenger.net_return_pct <= 0:
            failures.append("non_positive_return")
        if challenger.max_drawdown_pct >= 8 or challenger.hard_risk_violations:
            failures.append("hard_risk_failure")
        minimum_score = evidence.champion.score * 0.90 if evidence.champion.score > 0 else 0
        if challenger.score < minimum_score:
            failures.append("replay_underperformance")
        if (
            evidence.challenger_expected_shortfall_pct
            < evidence.champion_expected_shortfall_pct - 0.25
        ):
            failures.append("tail_risk_regression")
        status = "reject" if failures else "pass"
        reasons = tuple(failures)
    identity = hashlib.sha256(
        f"replay:{candidate_id}:{evaluated_at.isoformat()}".encode()
    ).hexdigest()[:32]
    return RuleReplayEvaluation(
        evaluation_id=identity,
        candidate_id=candidate_id,
        champion_id=champion_id,
        evaluated_at=evaluated_at,
        history_days=evidence.history_days,
        decision_coverage=evidence.decision_coverage,
        feature_coverage=evidence.feature_coverage,
        champion_score=evidence.champion.score,
        challenger_score=evidence.challenger.score,
        status=status,
        reasons=reasons,
    )


def _replay_pair(store, champion, challenger, snapshots):
    from intraday.replay import RecordedDecisionProvider, replay

    decision_by_snapshot = {
        decision.snapshot_id: decision
        for decision in store.list_recorded_decisions(
            limit=2_000_000,
            since=snapshots[0].event_time if snapshots else None,
        )
    }
    matched = [item for item in snapshots if item.snapshot_id in decision_by_snapshot]
    decisions = [decision_by_snapshot[item.snapshot_id] for item in matched]
    coverage = len(matched) / len(snapshots) if snapshots else 0
    if not matched:
        return None, None, coverage
    with tempfile.TemporaryDirectory(prefix="intraday-rule-replay-") as directory:
        champion_report = replay(
            matched,
            RecordedDecisionProvider(decisions),
            database=f"{directory}/champion.sqlite",
            rule=champion,
        )
        challenger_report = replay(
            matched,
            RecordedDecisionProvider(decisions),
            database=f"{directory}/challenger.sqlite",
            rule=challenger,
        )
    return champion_report, challenger_report, coverage


def _performance(report) -> Performance:
    return Performance(
        net_return_pct=report.net_return_pct,
        max_drawdown_pct=report.max_drawdown_pct,
        closed_trades=report.closed_trades,
    )


def _default_replay(*, candidate, champion, store, now):
    from intraday.replay import FULL_FEATURES

    bounds = store.snapshot_history_bounds()
    first = (
        datetime.fromisoformat(bounds["first_event_time"])
        if bounds["first_event_time"] else None
    )
    last = (
        datetime.fromisoformat(bounds["last_event_time"])
        if bounds["last_event_time"] else None
    )
    history_days = (last - first).total_seconds() / 86_400 if first and last else 0
    snapshots = (
        store.list_snapshots_since(last - timedelta(days=90), limit=2_000_000)
        if history_days >= 90 else []
    )
    required = len(snapshots) * len(FULL_FEATURES)
    present = sum(
        name in item.features and item.features[name] is not None
        for item in snapshots
        for name in FULL_FEATURES
    )
    feature_coverage = present / required if required else 0
    champion_report = challenger_report = None
    decision_coverage = 0
    if history_days >= 90 and feature_coverage >= 0.95:
        champion_report, challenger_report, decision_coverage = _replay_pair(
            store, champion, candidate, snapshots
        )
    baseline = _performance(champion_report) if champion_report else Performance(0, 0, 0)
    challenger = (
        _performance(challenger_report) if challenger_report else Performance(0, 0, 0)
    )
    return evaluate_replay_gate(
        candidate_id=candidate.rule_id,
        champion_id=champion.rule_id,
        evaluated_at=now,
        evidence=ReplayEvidence(
            history_days=max(0, history_days),
            decision_coverage=decision_coverage,
            feature_coverage=feature_coverage,
            champion=baseline,
            challenger=challenger,
            champion_expected_shortfall_pct=(
                champion_report.expected_shortfall_pct if champion_report else 0
            ),
            challenger_expected_shortfall_pct=(
                challenger_report.expected_shortfall_pct if challenger_report else 0
            ),
        ),
    )


def _default_promotion(*, candidate, champion, store, now, started_at):
    snapshots = store.list_snapshots_since(started_at, limit=2_000_000)
    champion_report, challenger_report, coverage = _replay_pair(
        store, champion, candidate, snapshots
    )
    baseline = _performance(champion_report) if champion_report else Performance(0, 0, 0)
    challenger = (
        _performance(challenger_report) if challenger_report else Performance(0, 0, 0)
    )
    return evaluate_promotion(
        candidate_id=candidate.rule_id,
        champion_id=champion.rule_id,
        started_at=started_at,
        evaluated_at=now,
        champion=baseline,
        challenger=challenger,
        coverage=coverage,
    )


def advance_rule_lifecycle(
    store,
    *,
    now: datetime,
    replay_evaluator=None,
    promotion_evaluator=None,
) -> dict:
    registry = store.rule_registry()
    if registry.get("challenger_id"):
        candidate = store.load_rule(registry["challenger_id"])
        champion = store.load_rule(registry["champion_id"])
        started_at = datetime.fromisoformat(registry["updated_at"])
        evaluator = promotion_evaluator or _default_promotion
        evaluation = evaluator(
            candidate=candidate,
            champion=champion,
            store=store,
            now=now,
            started_at=started_at,
        )
        store.record_rule_promotion_evaluation(evaluation)
        if evaluation.status == "promote":
            store.promote_challenger(now=now)
            return {"status": "promoted", "candidate_id": candidate.rule_id}
        if evaluation.status == "reject":
            store.reject_challenger(now=now)
            return {
                "status": "rejected",
                "candidate_id": candidate.rule_id,
                "reasons": evaluation.reasons,
            }
        return {
            "status": "challenger_deferred",
            "candidate_id": candidate.rule_id,
            "reasons": evaluation.reasons,
        }
    candidate = store.oldest_rule("queued")
    if candidate is None:
        return {"status": "idle"}
    champion = store.load_active_rule()
    if champion is None or candidate.parent_rule_id != champion.rule_id:
        store.update_rule_status(candidate.rule_id, expected="queued", status="rejected")
        return {"status": "rejected", "reason": "champion_lineage_mismatch"}
    evaluator = replay_evaluator or _default_replay
    evaluation = evaluator(
        candidate=candidate,
        champion=champion,
        store=store,
        now=now,
    )
    store.record_rule_replay_evaluation(evaluation)
    if evaluation.status == "deferred":
        return {
            "status": "deferred",
            "candidate_id": candidate.rule_id,
            "reasons": evaluation.reasons,
        }
    if evaluation.status == "reject":
        store.update_rule_status(candidate.rule_id, expected="queued", status="rejected")
        return {
            "status": "rejected",
            "candidate_id": candidate.rule_id,
            "reasons": evaluation.reasons,
        }
    store.update_rule_status(candidate.rule_id, expected="queued", status="replay_passed")
    store.set_challenger(candidate.rule_id, now=now)
    return {"status": "challenger_started", "candidate_id": candidate.rule_id}


def evaluate_promotion(
    *,
    candidate_id: str,
    champion_id: str,
    started_at: datetime,
    evaluated_at: datetime,
    champion: Performance,
    challenger: Performance,
    coverage: float,
) -> PromotionEvaluation:
    waiting = []
    if evaluated_at - started_at < timedelta(days=14):
        waiting.append("minimum_duration")
    if challenger.closed_trades < 30:
        waiting.append("minimum_closed_trades")
    if waiting:
        status = "deferred"
        reasons = tuple(waiting)
    else:
        failures = []
        if challenger.net_return_pct <= 0:
            failures.append("non_positive_return")
        if coverage < 0.99:
            failures.append("coverage_below_99pct")
        if challenger.max_drawdown_pct >= 8 or challenger.hard_risk_violations:
            failures.append("hard_risk_failure")
        required_score = champion.score * 1.10 if champion.score > 0 else 0.25
        if challenger.score < required_score:
            failures.append("insufficient_outperformance")
        status = "reject" if failures else "promote"
        reasons = tuple(failures)
    return PromotionEvaluation(
        candidate_id=candidate_id,
        champion_id=champion_id,
        started_at=started_at,
        evaluated_at=evaluated_at,
        coverage=coverage,
        champion_score=champion.score,
        challenger_score=challenger.score,
        status=status,
        reasons=reasons,
    )
