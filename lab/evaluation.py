"""Deterministic walk-forward promotion gate and one-shot holdout registry."""

import json
import math
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


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
                    holdout_opened_at TEXT
                )
                """
            )

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
        return result

    def open_holdout(self, candidate, metrics):
        state = self.get()
        if state["selected"] is None:
            raise ValueError("No selected candidate; account must stay cash")
        if candidate != state["selected"]:
            raise ValueError("Holdout must use the selected candidate")
        if state["holdout"] is not None:
            raise ValueError("Holdout was already opened")
        total_return = float(metrics["total_return"])
        max_drawdown = float(metrics["max_drawdown"])
        payload = {
            "candidate": candidate,
            "period": "2026-01-01/2026-08-31",
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "passed": total_return > 0 and max_drawdown >= -0.20,
        }
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE promotion_gate SET holdout_json=?,holdout_opened_at=? "
                "WHERE singleton=1 AND holdout_json IS NULL",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), _now()),
            )
        if cursor.rowcount != 1:
            raise ValueError("Holdout was already opened")
        return payload
