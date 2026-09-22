from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import (
    Direction,
    FeatureSnapshot,
    JevDecision,
    Regime,
    RiskLevel,
)
from intraday.replay import RecordedDecisionProvider, compare_cross_venue, replay


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snap(offset, price):
    at = NOW + timedelta(seconds=offset)
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=at,
        built_at=at,
        bid=price - 1,
        ask=price + 1,
        features={"price": price, "mark_price": price},
        freshness={"candles": True, "order_book": True},
    )


def decision(snapshot, direction):
    return JevDecision(
        decision_id=f"decision-{snapshot.snapshot_id}",
        tick_id="recorded",
        snapshot_id=snapshot.snapshot_id,
        direction=direction,
        direction_confidence=0.95,
        regime=Regime.TRENDING_UP,
        toxic_flow=0.1,
        entry_quality=4,
        risk_level=RiskLevel.LOW,
        model_ref="recorded/jev-v1",
        created_at=snapshot.event_time,
    )


def test_replay_uses_only_decision_bound_to_each_snapshot(tmp_path):
    first, second = snap(0, 100_000), snap(60, 101_000)
    provider = RecordedDecisionProvider([
        decision(first, Direction.BUY),
        decision(second, Direction.STRONG_SELL),
    ])

    report = replay(
        [first, second],
        provider,
        database=tmp_path / "replay.sqlite",
        initial_equity=10_000,
    )

    assert report.snapshots == 2
    assert report.decisions == 2
    assert report.fills == 2
    assert report.final_equity > 10_000
    assert report.fidelity == "partial"


def test_replay_rejects_out_of_order_inputs(tmp_path):
    first, second = snap(0, 100_000), snap(60, 101_000)

    with pytest.raises(ValueError, match="chronological"):
        replay(
            [second, first],
            RecordedDecisionProvider([]),
            database=tmp_path / "replay.sqlite",
        )


def test_cross_venue_comparison_reuses_identical_snapshots_and_decisions(tmp_path):
    first = snap(0, 100_000)
    first = FeatureSnapshot.create(
        symbol=first.symbol,
        event_time=first.event_time,
        built_at=first.built_at,
        bid=first.bid,
        ask=first.ask,
        features={
            **first.features,
            "xv_coverage_score": 1,
            "xv_mark_dislocation_bps": 0,
            "hl_return_30s_pct": -0.02,
            "hl_book_imbalance_10bps": -0.2,
        },
        freshness=first.freshness,
    )
    second = snap(60, 101_000)
    second = FeatureSnapshot.create(
        symbol=second.symbol,
        event_time=second.event_time,
        built_at=second.built_at,
        bid=second.bid,
        ask=second.ask,
        features={**second.features, "xv_coverage_score": 0},
        freshness=second.freshness,
    )
    decisions = [decision(first, Direction.BUY), decision(second, Direction.STRONG_SELL)]

    comparison = compare_cross_venue(
        [first, second], decisions, database_dir=tmp_path / "comparison"
    )

    assert comparison.baseline.snapshots == comparison.overlay.snapshots == 2
    assert comparison.baseline.decisions == comparison.overlay.decisions == 2
    assert comparison.overlay.final_equity < comparison.baseline.final_equity
