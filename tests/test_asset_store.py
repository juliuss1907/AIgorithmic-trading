import sqlite3
import json
from datetime import datetime, timezone
import pytest

from intraday.assets import ASSET_REGISTRY, AssetLifecycle, AssetStage
from intraday.contracts import DecisionScope, FeatureSnapshot
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)


def test_fresh_store_seeds_asset_scope_lifecycle_safely(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")

    lifecycles = store.list_asset_lifecycles()

    assert store.schema_version() == 22
    assert len(lifecycles) == len(ASSET_REGISTRY) * len(DecisionScope)
    assert {
        (item.symbol, item.scope, item.stage) for item in lifecycles
        if item.symbol == "BTCUSDT"
    } == {
        ("BTCUSDT", DecisionScope.SPOT_DAILY, AssetStage.SOAK),
        ("BTCUSDT", DecisionScope.PERP_INTRADAY, AssetStage.SOAK),
        ("BTCUSDT", DecisionScope.SPOT_4H, AssetStage.SHADOW),
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


def test_native_spot_candle_intervals_are_isolated_and_immutable(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    for interval, width in (("4h", 14_400_000), ("8h", 28_800_000),
                            ("1d", 86_400_000)):
        candle = [0, "100", "102", "99", "101", "10", width - 1]
        assert store.record_asset_candles("ETHUSDT", interval, [candle]) == 1
        assert store.record_asset_candles("ETHUSDT", interval, [candle]) == 0
        assert store.list_asset_candles("ETHUSDT", interval) == [candle]
        with pytest.raises(ValueError, match="historical"):
            store.record_asset_candles(
                "ETHUSDT", interval,
                [[0, "100", "103", "99", "101", "10", width - 1]],
            )


def test_v21_candle_constraint_migrates_without_losing_daily_history(tmp_path):
    database = tmp_path / "v21.sqlite"
    candle = [0, "100", "102", "99", "101", "10", 86_399_999]
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE asset_daily_candles (
                symbol TEXT NOT NULL, interval TEXT NOT NULL CHECK (interval='1d'),
                open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
                payload_json TEXT NOT NULL, PRIMARY KEY (symbol, interval, open_time)
            );
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '21');
        """)
        connection.execute(
            "INSERT INTO asset_daily_candles VALUES (?, ?, ?, ?, ?)",
            ("BTCUSDT", "1d", 0, 86_399_999, json.dumps(candle)),
        )
    store = IntradayStore(database)
    assert store.schema_version() == 22
    assert store.list_asset_daily_candles("BTCUSDT") == [candle]
    assert store.record_asset_candles(
        "BTCUSDT", "4h", [[0, "100", "102", "99", "101", "10", 14_399_999]]
    ) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


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
    assert store.schema_version() == 22
    assert snapshot_symbol == "BTCUSDT"
    assert soak_symbol == "BTCUSDT"
