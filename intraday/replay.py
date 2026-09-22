"""Chronological replay over immutable snapshots and recorded model decisions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from intraday.contracts import FeatureSnapshot, JevDecision, RuleCandidate
from intraday.engine import IntradayEngine
from intraday.store import IntradayStore


FULL_FEATURES = {
    "price",
    "mark_price",
    "index_price",
    "basis_bps",
    "volume_1h",
    "rsi14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_mid",
    "bb_upper",
    "bb_lower",
    "funding_rate",
    "open_interest",
    "long_short_ratio",
    "order_book_imbalance",
    "spread_bps",
    "buy_ratio",
    "path_efficiency",
    "sentiment_score",
}


class RecordedDecisionProvider:
    model_ref = "recorded/replay"

    def __init__(self, decisions: list[JevDecision]):
        self._decisions = {item.snapshot_id: item for item in decisions}
        if len(self._decisions) != len(decisions):
            raise ValueError("only one recorded decision is allowed per snapshot")
        self.hits = 0

    def decide(self, snapshot: FeatureSnapshot, tick_id: str, now) -> JevDecision:
        decision = self._decisions[snapshot.snapshot_id]
        self.hits += 1
        return decision.model_copy(update={"tick_id": tick_id, "created_at": now})


@dataclass(frozen=True)
class ReplayReport:
    snapshots: int
    decisions: int
    fills: int
    final_equity: float
    net_return_pct: float
    max_drawdown_pct: float
    expected_shortfall_pct: float
    closed_trades: int
    decision_coverage: float
    feature_coverage: float
    fidelity: str


@dataclass(frozen=True)
class CrossVenueReplayComparison:
    baseline: ReplayReport
    overlay: ReplayReport


def replay(
    snapshots: list[FeatureSnapshot],
    provider: RecordedDecisionProvider,
    *,
    database: str | Path,
    initial_equity: float = 10_000,
    cross_venue_mode: str = "off",
    rule: RuleCandidate | None = None,
) -> ReplayReport:
    if any(
        current.event_time <= previous.event_time
        for previous, current in zip(snapshots, snapshots[1:])
    ):
        raise ValueError("snapshots must be strictly chronological")
    store = IntradayStore(database)
    if rule is not None:
        store.register_rule(rule, status="champion")
        store.activate_champion(rule.rule_id, now=snapshots[0].built_at)
    engine = IntradayEngine(
        store,
        provider,
        initial_equity=initial_equity,
        cross_venue_mode=cross_venue_mode,
    )
    peak = initial_equity
    maximum_drawdown = 0.0
    previous_equity = initial_equity
    equity_returns: list[float] = []
    closed_trades = 0
    present = required = 0
    for snapshot in snapshots:
        result = engine.run_tick(snapshot, now=snapshot.built_at)
        equity = engine.portfolio.equity(result.position.mark_price)
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - 1)
        equity_returns.append((equity / previous_equity - 1) * 100)
        previous_equity = equity
        if result.fill is not None and result.fill.reduce_only and result.position.tranches == 0:
            closed_trades += 1
        required += len(FULL_FEATURES)
        present += sum(
            name in snapshot.features and snapshot.features[name] is not None
            for name in FULL_FEATURES
        )
    counts = store.counts()
    mark = snapshots[-1].features.get("mark_price") if snapshots else None
    final_equity = engine.portfolio.equity(float(mark)) if mark is not None else initial_equity
    feature_coverage = present / required if required else 0.0
    tail_size = max(1, math.ceil(len(equity_returns) * 0.05)) if equity_returns else 0
    expected_shortfall = (
        sum(sorted(equity_returns)[:tail_size]) / tail_size if tail_size else 0.0
    )
    return ReplayReport(
        snapshots=len(snapshots),
        decisions=counts["decisions"],
        fills=counts["fills"],
        final_equity=final_equity,
        net_return_pct=(final_equity / initial_equity - 1) * 100,
        max_drawdown_pct=abs(maximum_drawdown) * 100,
        expected_shortfall_pct=expected_shortfall,
        closed_trades=closed_trades,
        decision_coverage=provider.hits / len(snapshots) if snapshots else 0.0,
        feature_coverage=feature_coverage,
        fidelity="full" if feature_coverage == 1.0 else "partial",
    )


def compare_cross_venue(
    snapshots: list[FeatureSnapshot],
    decisions: list[JevDecision],
    *,
    database_dir: str | Path,
    initial_equity: float = 10_000,
) -> CrossVenueReplayComparison:
    root = Path(database_dir)
    root.mkdir(parents=True, exist_ok=True)
    baseline = replay(
        snapshots,
        RecordedDecisionProvider(decisions),
        database=root / "baseline.sqlite",
        initial_equity=initial_equity,
        cross_venue_mode="off",
    )
    overlay = replay(
        snapshots,
        RecordedDecisionProvider(decisions),
        database=root / "overlay.sqlite",
        initial_equity=initial_equity,
        cross_venue_mode="active",
    )
    return CrossVenueReplayComparison(baseline=baseline, overlay=overlay)
