"""Deterministic walk-forward promotion gate and one-shot holdout registry."""

import hashlib
import json
import math
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from lab.contracts import CandidateLock, ExperimentSpec, HoldoutResult


LEARNING_FOLDS = tuple(str(year) for year in range(2018, 2026))


@dataclass(frozen=True)
class CandidateScore:
    name: str
    passed: bool
    profitable_folds: int
    aggregate_stress_return: float
    worst_drawdown: float
    median_return: float
    total_round_trips: int
    failure_reasons: tuple[str, ...]


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    selected: str | None
    scores: tuple[CandidateScore, ...]


def _metric(summary, fold, cost_bps):
    key = f"{fold}/{cost_bps:g}bps/rule"
    try:
        return summary[key]
    except KeyError as exc:
        raise ValueError(f"Missing promotion artifact: {key}") from exc


def score_candidate(name, summary, base_cost_bps=5, stress_cost_bps=10):
    base = [_metric(summary, fold, base_cost_bps) for fold in LEARNING_FOLDS]
    stress = [_metric(summary, fold, stress_cost_bps) for fold in LEARNING_FOLDS]
    returns = [float(metric["total_return"]) for metric in base]
    stress_return = math.prod(1 + float(metric["total_return"]) for metric in stress) - 1
    worst_drawdown = min(float(metric["max_drawdown"]) for metric in base)
    profitable = sum(value > 0 for value in returns)
    reasons = []
    if profitable < 5:
        reasons.append("profitable_folds")
    if stress_return <= 0:
        reasons.append("stress_return")
    if worst_drawdown < -0.20:
        reasons.append("drawdown")
    return CandidateScore(
        name=name,
        passed=not reasons,
        profitable_folds=profitable,
        aggregate_stress_return=stress_return,
        worst_drawdown=worst_drawdown,
        median_return=statistics.median(returns),
        total_round_trips=sum(int(metric["round_trips"]) for metric in base),
        failure_reasons=tuple(reasons),
    )


def select_candidate(candidate_summaries):
    scores = tuple(
        score_candidate(name, summary) for name, summary in sorted(candidate_summaries.items())
    )
    passing = [score for score in scores if score.passed]
    if not passing:
        return PromotionDecision("stay_cash", None, scores)
    selected = min(
        passing,
        key=lambda item: (
            abs(item.worst_drawdown), -item.median_return,
            item.total_round_trips, item.name,
        ),
    )
    return PromotionDecision("candidate_selected", selected.name, scores)


def _now():
    return datetime.now(timezone.utc).isoformat()


class PromotionStore:
    """A durable latch: a decision freezes before holdout metrics can be recorded."""

    def __init__(self, database):
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS promotion_gate (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    decision_json TEXT NOT NULL,
                    frozen_at TEXT NOT NULL,
                    holdout_json TEXT,
                    holdout_opened_at TEXT,
                    candidate_lock_json TEXT,
                    candidate_locked_at TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(promotion_gate)")}
            if "candidate_lock_json" not in columns:
                connection.execute("ALTER TABLE promotion_gate ADD COLUMN candidate_lock_json TEXT")
            if "candidate_locked_at" not in columns:
                connection.execute("ALTER TABLE promotion_gate ADD COLUMN candidate_locked_at TEXT")

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _decision_payload(decision):
        return {
            "status": decision.status,
            "selected": decision.selected,
            "scores": [asdict(score) for score in decision.scores],
        }

    def freeze(self, decision):
        payload = self._decision_payload(decision)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO promotion_gate (singleton,decision_json,frozen_at) VALUES (1,?,?)",
                    (json.dumps(payload, sort_keys=True, separators=(",", ":")), _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Promotion gate is already frozen") from exc
        return self.get()

    def get(self):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM promotion_gate WHERE singleton=1").fetchone()
        if row is None:
            raise KeyError("Promotion gate is not frozen")
        result = json.loads(row["decision_json"])
        result["frozen_at"] = row["frozen_at"]
        result["holdout_opened_at"] = row["holdout_opened_at"]
        result["holdout"] = json.loads(row["holdout_json"]) if row["holdout_json"] else None
        result["candidate_locked_at"] = row["candidate_locked_at"]
        result["candidate_lock"] = (
            json.loads(row["candidate_lock_json"]) if row["candidate_lock_json"] else None
        )
        return result

    def lock_candidate(self, candidate_lock):
        lock = CandidateLock.model_validate(candidate_lock)
        payload = json.dumps(lock.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT decision_json,candidate_lock_json,holdout_json "
                "FROM promotion_gate WHERE singleton=1"
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise KeyError("Promotion gate is not frozen")
            if json.loads(row["decision_json"])["selected"] != lock.candidate:
                connection.execute("ROLLBACK")
                raise ValueError("Candidate lock must match the selected candidate")
            if row["holdout_json"] is not None:
                connection.execute("ROLLBACK")
                raise ValueError("Candidate cannot be locked after holdout opens")
            if row["candidate_lock_json"] is not None:
                connection.execute("COMMIT")
                if row["candidate_lock_json"] != payload:
                    raise ValueError("Candidate is already locked with a different payload")
                return json.loads(payload)
            connection.execute(
                "UPDATE promotion_gate SET candidate_lock_json=?,candidate_locked_at=? "
                "WHERE singleton=1 AND candidate_lock_json IS NULL",
                (payload, _now()),
            )
            connection.execute("COMMIT")
        return json.loads(payload)

    def open_holdout(self, evidence):
        evidence = HoldoutResult.model_validate(evidence)
        payload = evidence.model_dump(mode="json")
        state = self.get()
        lock = state.get("candidate_lock")
        if lock is None:
            raise ValueError("Holdout requires a locked candidate")
        if evidence.candidate != state["selected"] or evidence.candidate != lock["candidate"]:
            raise ValueError("Holdout must use the locked selected candidate")
        if state["holdout"] is not None:
            if state["holdout"] == payload:
                return payload
            raise ValueError("Holdout was already opened with different evidence")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE promotion_gate SET holdout_json=?,holdout_opened_at=? "
                "WHERE singleton=1 AND holdout_json IS NULL",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), _now()),
            )
        if cursor.rowcount != 1:
            raise ValueError("Holdout was already opened")
        return payload


def _verified_artifact(run_store, run_id, relative_path):
    artifact = next(
        (item for item in run_store.list_artifacts(run_id) if item["relative_path"] == relative_path),
        None,
    )
    if artifact is None:
        raise ValueError(f"Registered run is missing {relative_path}")
    return run_store.artifact(run_id, artifact["id"])[0]


def lock_selected_candidate(promotion_store, run_store, run_id):
    """Verify the selected learning run, then durably bind it to the frozen gate."""
    state = promotion_store.get()
    candidate = state.get("selected")
    if candidate is None:
        raise ValueError("No selected candidate; account must stay cash")
    run = run_store.get_run(run_id)
    summary_path = _verified_artifact(run_store, run_id, "summary.json")
    provenance_path = _verified_artifact(run_store, run_id, "provenance.json")
    summary = json.loads(summary_path.read_text())
    actual_score = asdict(score_candidate(candidate, summary))
    frozen_score = next(
        (score for score in state["scores"] if score["name"] == candidate), None
    )
    if frozen_score is None or json.dumps(actual_score, sort_keys=True) != json.dumps(
        frozen_score, sort_keys=True
    ):
        raise ValueError("Candidate run score differs from the frozen gate score")
    provenance = json.loads(provenance_path.read_text())
    config = ExperimentSpec.model_validate(provenance["experiment"])
    if config.strategy.family != candidate or run["dataset_id"] != config.dataset_id:
        raise ValueError("Candidate run contract differs from the frozen selection")
    if tuple(config.periods) != LEARNING_FOLDS or tuple(config.prior_observed_periods) != LEARNING_FOLDS:
        raise ValueError("Candidate run does not contain the registered learning folds")
    lock = CandidateLock(
        candidate=candidate,
        run_id=run_id,
        dataset_id=config.dataset_id,
        parent_run_id=config.parent_run_id,
        strategy=config.strategy,
        position_sizing=config.risk_policy.position_sizing,
        market=config.market,
        venue=config.venue,
        symbol=config.symbol,
        interval=config.interval,
        calendar=config.calendar,
        data_start=str(config.data.start),
        learning_end_exclusive=str(config.data.end_exclusive),
        initial_cash=config.initial_cash,
        slippage_bps=config.slippage_bps,
        taker_fee_bps=config.taker_fee_bps,
        commission=config.commission,
        max_target_weight=config.risk_policy.max_target_weight,
        halt_drawdown=config.risk_policy.halt_drawdown,
        summary_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        provenance_sha256=hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
    )
    return promotion_store.lock_candidate(lock)
