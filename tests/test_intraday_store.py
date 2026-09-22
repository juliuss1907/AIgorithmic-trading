from datetime import datetime, timezone

from intraday.contracts import (
    Direction,
    FeatureSnapshot,
    GateDecision,
    JevDecision,
    PaperFill,
    Regime,
    RiskLevel,
)
from intraday.news import NewsIntelligence
from tests.test_intraday_news import event
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def records():
    snapshot = FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=NOW,
        built_at=NOW,
        bid=99_990,
        ask=100_010,
        features={"price": 100_000},
        freshness={"book": True},
    )
    decision = JevDecision(
        decision_id="decision-1",
        tick_id="2026-09-21T12:00:00Z:BTCUSDT",
        snapshot_id=snapshot.snapshot_id,
        direction=Direction.BUY,
        direction_confidence=0.9,
        regime=Regime.TRENDING_UP,
        toxic_flow=0.1,
        entry_quality=4,
        risk_level=RiskLevel.LOW,
        model_ref="stub/jev-v1",
        created_at=NOW,
    )
    gate = GateDecision(
        gate_id="gate-1",
        decision_id=decision.decision_id,
        rule_id="rule-v1",
        outcome="authorized",
        target_tranches=1,
        authorized_notional=2_500,
        reason_codes=(),
        evaluated_at=NOW,
    )
    fill = PaperFill(
        fill_id="fill-1",
        order_id="order-1",
        gate_id=gate.gate_id,
        side="buy",
        quantity=0.025,
        price=100_020,
        notional=2_500.5,
        fee=1.0,
        slippage=0.25,
        reduce_only=False,
        filled_at=NOW,
    )
    return snapshot, decision, gate, fill


def test_record_tick_is_atomic_and_idempotent(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    snapshot, decision, gate, fill = records()

    first = store.record_tick(snapshot, decision, gate, fill)
    repeated = store.record_tick(snapshot, decision, gate, fill)

    assert repeated == first
    assert store.counts() == {"snapshots": 1, "decisions": 1, "gates": 1, "fills": 1}
    assert store.list_decisions(limit=10)[0]["direction"] == "Buy"


def test_command_inbox_deduplicates_operator_requests(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")

    first = store.enqueue_command("command-1", "pause_entries", NOW, actor="operator")
    repeated = store.enqueue_command("command-1", "pause_entries", NOW, actor="operator")

    assert repeated == first
    assert store.list_commands(status="pending") == [first]


def test_runtime_state_round_trips_as_json(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    state = {"halted": True, "high_water_mark": 10_123.5}

    store.save_runtime_state(state, updated_at=NOW)

    assert store.load_runtime_state() == state


def test_news_events_round_trip_without_duplicates(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    result = NewsIntelligence().ingest(
        [event("sec", "A", "Emergency BTC market action announced", event_id="news-1")],
        now=NOW,
    )

    store.record_news(result)
    store.record_news(result)

    loaded = store.list_news_events(limit=10)
    assert len(loaded) == 1
    assert loaded[0].event_id == "news-1"
