import sqlite3
from datetime import datetime, timezone

from intraday.assets import ASSET_REGISTRY, AssetLifecycle, AssetStage
from intraday.contracts import DecisionScope, FeatureSnapshot
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)


def test_fresh_store_seeds_asset_scope_lifecycle_safely(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")

    lifecycles = store.list_asset_lifecycles()

    assert store.schema_version() == 20
    assert len(lifecycles) == len(ASSET_REGISTRY) * len(DecisionScope)
    assert {
        (item.symbol, item.scope, item.stage) for item in lifecycles
        if item.symbol == "BTCUSDT"
    } == {
        ("BTCUSDT", scope, AssetStage.SOAK) for scope in DecisionScope
    }
    assert all(
        item.stage is AssetStage.SHADOW
        for item in lifecycles
        if item.symbol != "BTCUSDT"
    )


def test_asset_lifecycle_round_trips_by_symbol_and_scope(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    eth = store.asset_lifecycle("ETHUSDT", DecisionScope.PERP_INTRADAY)

    stored = store.save_asset_lifecycle(eth.start_soak(), updated_at=NOW)

    assert stored == AssetLifecycle(
        symbol="ETHUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        stage=AssetStage.SOAK,
    )
    assert store.asset_lifecycle(
        "ETHUSDT", DecisionScope.SPOT_DAILY
    ).stage is AssetStage.SHADOW


def test_legacy_snapshot_readers_remain_btc_scoped(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    btc = FeatureSnapshot.create(
        symbol="BTCUSDT", event_time=NOW, built_at=NOW,
        bid=99, ask=101, features={"price": 100}, freshness={"book": True},
    )
    eth = FeatureSnapshot.create(
        symbol="ETHUSDT", event_time=NOW, built_at=NOW,
        bid=3_999, ask=4_001, features={"price": 4_000}, freshness={"book": True},
    )
    store.record_snapshot(btc)
    store.record_snapshot(eth)

    assert store.latest_snapshot() == btc
    assert store.list_snapshots() == [btc]
    assert store.latest_snapshot(symbol="ETHUSDT") == eth


def test_schema_v19_backfills_btc_snapshot_and_soak_ownership(tmp_path):
    database = tmp_path / "intraday.sqlite"
    snapshot = FeatureSnapshot.create(
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
                payload_json TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'binance_usdm_perp'
            );
            CREATE TABLE portfolio_soak_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                status TEXT NOT NULL,
                hard_risk_violation INTEGER NOT NULL DEFAULT 0,
                evidence_version TEXT NOT NULL DEFAULT 'scope-price-v2',
                created_at TEXT NOT NULL
            );
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '19');
            """
        )
        connection.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
            (snapshot.snapshot_id, NOW.isoformat(), snapshot.model_dump_json(), snapshot.market),
        )
        connection.execute(
            "INSERT INTO portfolio_soak_ticks "
            "(scope, status, created_at) VALUES (?, ?, ?)",
            (DecisionScope.PERP_INTRADAY.value, "success", NOW.isoformat()),
        )

    store = IntradayStore(database)

    with sqlite3.connect(database) as connection:
        snapshot_symbol = connection.execute(
            "SELECT symbol FROM snapshots WHERE id=?", (snapshot.snapshot_id,)
        ).fetchone()[0]
        soak_symbol = connection.execute(
            "SELECT symbol FROM portfolio_soak_ticks"
        ).fetchone()[0]
    assert store.schema_version() == 20
    assert snapshot_symbol == "BTCUSDT"
    assert soak_symbol == "BTCUSDT"
