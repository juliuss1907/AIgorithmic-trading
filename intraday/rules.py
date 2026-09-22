"""Bounded rule-candidate evaluation; no generated code is ever executed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from intraday.contracts import PromotionEvaluation


@dataclass(frozen=True)
class Performance:
    net_return_pct: float
    max_drawdown_pct: float
    closed_trades: int
    hard_risk_violations: int = 0

    @property
    def score(self) -> float:
        return self.net_return_pct / max(self.max_drawdown_pct, 0.5)


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
