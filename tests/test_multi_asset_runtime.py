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
    ScopedRuleCandidate,
    SpotRuleParameters,
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
    spot = scope in {DecisionScope.SPOT_DAILY, DecisionScope.SPOT_4H}
    return FeatureSnapshot.create(
        symbol=symbol,
        market="binance_spot" if spot else "binance_usdm_perp",
        timeframe="4h" if scope is DecisionScope.SPOT_4H else "1d" if spot else "1h",
        feature_schema_version="3" if scope is DecisionScope.SPOT_4H else "2",
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
            else "spot_4h_entry" if scope is DecisionScope.SPOT_4H
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

    assert results == ["shadow_recorded"] * 15
    assert provider.calls == []
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 15
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


def test_eth_spot_soak_does_not_inherit_btc_rule_or_call_model(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    provider = Provider()
    lifecycle = store.asset_lifecycle("ETHUSDT", DecisionScope.SPOT_DAILY)
    store.save_asset_lifecycle(lifecycle.start_soak(), updated_at=NOW)

    status = run_asset_lifecycle_observation(
        store,
        provider,
        snapshot("ETHUSDT", DecisionScope.SPOT_DAILY),
        scope=DecisionScope.SPOT_DAILY,
        now=NOW,
    )

    assert status == "skipped_no_setup"
    assert provider.calls == []
    assert store.list_portfolio_soak_ticks(symbol="ETHUSDT")[0]["scope"] == "spot_daily"


def test_eth_spot_candidate_calls_model_only_on_setup_and_records_no_fill(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    provider = Provider()
    candidate = ScopedRuleCandidate.create(
        rule_id="eth-spot-baseline", parent_rule_id="bootstrap", thesis_id="baseline",
        symbol="ETHUSDT", scope=DecisionScope.SPOT_DAILY,
        parameters=SpotRuleParameters(), created_at=NOW,
        model_ref="deterministic/baseline", prompt_version="spot-baseline-v1",
    )
    store.register_scoped_rule(candidate, status="replay_passed")
    store.set_scoped_challenger(candidate.rule_id, now=NOW)
    lifecycle = store.asset_lifecycle("ETHUSDT", DecisionScope.SPOT_DAILY)
    store.save_asset_lifecycle(lifecycle.start_soak(), updated_at=NOW)
    candles = []
    for index in range(35):
        price = 100 + index
        candles.append([index * 86_400_000, str(price), str(price + 0.5),
                        str(price - 0.5), str(price), "10",
                        index * 86_400_000 + 86_399_999])

    status = run_asset_lifecycle_observation(
        store, provider, snapshot("ETHUSDT", DecisionScope.SPOT_DAILY),
        scope=DecisionScope.SPOT_DAILY, now=NOW,
        spot_rule=candidate, spot_candles=candles,
    )

    assert status == "success"
    assert provider.calls == [("ETHUSDT", DecisionScope.SPOT_DAILY)]
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM parent_paper_fills").fetchone()[0] == 0
        assert connection.execute(
            "SELECT symbol, candidate_id FROM scoped_rule_soak_ticks"
        ).fetchone() == ("ETHUSDT", candidate.rule_id)


def test_hype_spot_4h_decision_soak_calls_model_but_cannot_enter_paper(tmp_path):
    store = IntradayStore(tmp_path / "test.sqlite")
    provider = Provider()
    candidate = ScopedRuleCandidate.create(
        rule_id="hype-spot-4h-baseline", parent_rule_id="bootstrap",
        thesis_id="baseline", symbol="HYPEUSDT", scope=DecisionScope.SPOT_4H,
        parameters=SpotRuleParameters(), created_at=NOW,
        model_ref="deterministic/baseline", prompt_version="spot-4h-baseline-v1",
    )
    store.register_scoped_rule(candidate, status="replay_passed")
    store.set_scoped_challenger(candidate.rule_id, now=NOW)
    store.save_asset_lifecycle(
        store.asset_lifecycle("HYPEUSDT", DecisionScope.SPOT_4H).start_soak(),
        updated_at=NOW,
    )
    candles = [[index * 14_400_000, str(100 + index), str(100.5 + index),
                str(99 + index), str(100 + index), "10",
                (index + 1) * 14_400_000 - 1] for index in range(35)]
    status = run_asset_lifecycle_observation(
        store, provider, snapshot("HYPEUSDT", DecisionScope.SPOT_4H),
        scope=DecisionScope.SPOT_4H, now=NOW,
        spot_rule=candidate, spot_candles=candles,
    )
    assert status == "success"
    assert provider.calls == [("HYPEUSDT", DecisionScope.SPOT_4H)]
    assert store.list_portfolio_soak_ticks(symbol="HYPEUSDT")[0]["scope"] == "spot_4h"
    assert store.list_parent_paper_fills() == []
