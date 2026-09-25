import sqlite3
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


def test_snapshots_are_partitioned_by_market_without_mixing_replay_data(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    perp = FeatureSnapshot.create(
        symbol="BTCUSDT",
        market="binance_usdm_perp",
        timeframe="1h",
        feature_schema_version="2",
        event_time=NOW,
        built_at=NOW,
        bid=99_990,
        ask=100_010,
        features={"price": 99_000, "reference_price": 100_000},
        freshness={"book": True},
    )
    spot = FeatureSnapshot.create(
        symbol="BTCUSDT",
        market="binance_spot",
        timeframe="1d",
        feature_schema_version="2",
        event_time=NOW,
        built_at=NOW,
        bid=98_990,
        ask=99_010,
        features={"price": 98_000, "reference_price": 99_000},
        freshness={"book": True},
    )

    store.record_snapshot(perp)
    store.record_snapshot(spot)

    assert store.latest_snapshot().snapshot_id == perp.snapshot_id
    assert store.latest_snapshot(market="binance_spot").snapshot_id == spot.snapshot_id
    assert store.list_snapshots() == [perp]
    assert store.list_snapshots(market="binance_spot") == [spot]


def test_schema_v17_snapshot_and_soak_rows_upgrade_without_data_loss(tmp_path):
    database = tmp_path / "intraday.sqlite"
    legacy_snapshot = FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=NOW,
        built_at=NOW,
        bid=99_990,
        ask=100_010,
        features={"price": 100_000},
        freshness={"book": True},
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE snapshots (
                id TEXT PRIMARY KEY,
                event_time TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE portfolio_soak_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                status TEXT NOT NULL,
                hard_risk_violation INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '17');
            """
        )
        connection.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?)",
            (
                legacy_snapshot.snapshot_id,
                NOW.isoformat(),
                legacy_snapshot.model_dump_json(),
            ),
        )
        connection.execute(
            "INSERT INTO portfolio_soak_ticks "
            "(scope, status, hard_risk_violation, created_at) VALUES (?, ?, ?, ?)",
            ("perp_intraday", "success", 0, NOW.isoformat()),
        )

    store = IntradayStore(database)

    assert store.schema_version() == 18
    assert store.latest_snapshot().snapshot_id == legacy_snapshot.snapshot_id
    assert store.list_portfolio_soak_ticks(evidence_version="market-v1") == [
        {
            "scope": "perp_intraday",
            "status": "success",
            "hard_risk_violation": 0,
            "evidence_version": "market-v1",
            "created_at": NOW.isoformat(),
        }
    ]


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


def test_scheduler_slots_are_claimed_once_across_store_instances(tmp_path):
    database = tmp_path / "intraday.sqlite"
    first = IntradayStore(database)
    restarted = IntradayStore(database)

    assert first.claim_scheduler_run("perp_numeric", NOW) is True
    assert restarted.claim_scheduler_run("perp_numeric", NOW) is False

    first.finish_scheduler_run("perp_numeric", NOW, status="success")
    record = restarted.scheduler_run("perp_numeric", NOW)
    assert record["status"] == "success"
    assert record["finished_at"] is not None


def test_operational_health_reports_latest_scheduler_run_and_schema(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    later = datetime(2026, 9, 21, 12, 1, tzinfo=timezone.utc)

    assert store.claim_scheduler_run("perp_numeric", NOW, started_at=NOW)
    store.finish_scheduler_run(
        "perp_numeric", NOW, status="success", finished_at=NOW
    )
    assert store.claim_scheduler_run("perp_numeric", later, started_at=later)
    store.finish_scheduler_run(
        "perp_numeric",
        later,
        status="error",
        error_code="ProviderTimeout",
        finished_at=later,
    )

    assert store.schema_version() == 18
    assert store.latest_scheduler_runs() == [
        {
            "job_name": "perp_numeric",
            "scheduled_for": later.isoformat(),
            "status": "error",
            "started_at": later.isoformat(),
            "finished_at": later.isoformat(),
            "error_code": "ProviderTimeout",
        }
    ]
