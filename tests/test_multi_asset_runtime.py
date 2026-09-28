import sqlite3
from datetime import datetime, timezone

from intraday.contracts import (
    DecisionScope,
    Direction,
    FeatureSnapshot,
    JevDecision,
    Regime,
    RiskLevel,
    ScopedJevDecision,
)
from intraday.portfolio_soak import run_asset_lifecycle_observation
from intraday.providers import JevDecisionProvider
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)


def test_provider_questions_name_the_snapshot_asset():
    questions = JevDecisionProvider._questions(
        DecisionScope.PERP_INTRADAY, "ETHUSDT"
    )

    assert "ETHUSDT perpetual" in questions["direction"]["instructions"]


def snapshot(symbol: str, scope: DecisionScope) -> FeatureSnapshot:
    spot = scope is DecisionScope.SPOT_DAILY
    return FeatureSnapshot.create(
        symbol=symbol,
        market="binance_spot" if spot else "binance_usdm_perp",
        timeframe="1d" if spot else "1h",
        feature_schema_version="2",
        event_time=NOW,
        built_at=NOW,
        bid=99,
        ask=101,
        features={"price": 100, "reference_price": 100},
        freshness={"candles": True, "order_book": True},
    )


class Provider:
    def __init__(self):
        self.calls = []

    def decide_scoped(self, market, tick_id, scope, now):
        self.calls.append((market.symbol, scope))
        decision = JevDecision(
            decision_id=f"decision-{market.symbol}-{scope.value}",
            tick_id=tick_id,
            snapshot_id=market.snapshot_id,
            direction=Direction.HOLD,
            direction_confidence=0.91,
            regime=Regime.SIDEWAYS,
            toxic_flow=0.1,
            entry_quality=4,
            risk_level=RiskLevel.LOW,
            model_ref="fake/jev",
            created_at=now,
        )
        workflow = (
            "spot_daily_entry"
            if scope is DecisionScope.SPOT_DAILY
            else "perp_intraday_entry"
        )
        return ScopedJevDecision(scope=scope, workflow=workflow, decision=decision)


def test_shadow_assets_persist_market_data_without_models_signals_or_fills(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    provider = Provider()

    results = [
        run_asset_lifecycle_observation(
            store, provider, snapshot(symbol, scope), scope=scope, now=NOW
        )
        for symbol in ("ETHUSDT", "HYPEUSDT", "NEARUSDT", "ZECUSDT", "SOLUSDT")
        for scope in DecisionScope
    ]

    assert results == ["shadow_recorded"] * 10
    assert provider.calls == []
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM parent_paper_fills").fetchone()[0] == 0


def test_eth_soak_calls_model_and_journals_only_promoted_scope(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    provider = Provider()
    lifecycle = store.asset_lifecycle("ETHUSDT", DecisionScope.PERP_INTRADAY)
    store.save_asset_lifecycle(lifecycle.start_soak(), updated_at=NOW)

    status = run_asset_lifecycle_observation(
        store,
        provider,
        snapshot("ETHUSDT", DecisionScope.PERP_INTRADAY),
        scope=DecisionScope.PERP_INTRADAY,
        now=NOW,
    )

    assert status == "success"
    assert provider.calls == [("ETHUSDT", DecisionScope.PERP_INTRADAY)]
    assert store.list_portfolio_soak_ticks(symbol="ETHUSDT") == [{
        "scope": "perp_intraday",
        "status": "success",
        "hard_risk_violation": 0,
        "evidence_version": "scope-price-v2",
        "created_at": NOW.isoformat(),
    }]
    with sqlite3.connect(store.database) as connection:
        row = connection.execute(
            "SELECT symbol, scope, gate_passed, gate_reason FROM signals"
        ).fetchone()
    assert row == ("ETHUSDT", "perp_intraday", 0, "soak_observation_only")
