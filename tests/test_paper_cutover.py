import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from intraday.__main__ import main
from intraday.contracts import (
    DecisionScope, FeatureSnapshot, PerpRuleParameters, ScopedRuleCandidate,
)
from intraday.market import StaleMarketData
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.store import IntradayStore


def test_paper_worker_keeps_perp_running_without_spot_champion_or_1d_entries(
    tmp_path, monkeypatch, capsys,
):
    database = tmp_path / "paper.sqlite"
    now = datetime.now(timezone.utc)
    store = IntradayStore(database)
    store.save_parent_portfolio_state(ParentPortfolioState(
        mark_price=100, spot_price=100, perp_mark_price=100,
        day_start_equity=10_000, high_water_mark=10_000,
        entries_paused=False, paper_active=True, updated_at=now,
    ), event_kind="activated", actor="test")
    perp = ScopedRuleCandidate.create(
        rule_id="btc-perp-test-champion", parent_rule_id="bootstrap",
        thesis_id="baseline", symbol="BTCUSDT", scope=DecisionScope.PERP_INTRADAY,
        parameters=PerpRuleParameters(), created_at=now,
        model_ref="deterministic/baseline", prompt_version="test-v1",
    )
    store.register_scoped_rule(perp, status="champion")
    store.activate_scoped_champion(perp.scope, perp.rule_id, now=now)

    def perp_snapshot(self, symbol, *, now, **kwargs):
        return FeatureSnapshot.create(
            symbol=symbol, market="binance_usdm_perp", timeframe="1h",
            feature_schema_version="2", event_time=now, built_at=now,
            bid=99, ask=101,
            features={"price": 100, "mark_price": 100, "reference_price": 100},
            freshness={"candles": True, "order_book": True},
        )

    def daily_snapshot(self, symbol, *, now, **kwargs):
        return FeatureSnapshot.create(
            symbol=symbol, market="binance_spot", timeframe="1d",
            feature_schema_version="2", event_time=now, built_at=now,
            bid=99, ask=101,
            features={"price": 100, "reference_price": 100},
            freshness={"candles": True, "order_book": True},
        )

    def daily_candles(self):
        width = 86_400_000
        last = int(now.timestamp() * 1000) // width * width - width
        return [[last - (39 - index) * width, str(100 + index),
                 str(100.5 + index), str(99 + index), str(100 + index), "10",
                 last - (38 - index) * width - 1] for index in range(40)]

    monkeypatch.setattr("intraday.__main__.MultiCadenceMarketCache.snapshot", perp_snapshot)
    monkeypatch.setattr("intraday.__main__.MultiCadenceSpotCache.snapshot", daily_snapshot)
    monkeypatch.setattr("intraday.__main__.MultiCadenceSpotCache.closed_candles", daily_candles)
    monkeypatch.setattr(
        "intraday.__main__.MultiTimeframeSpotCache.snapshot",
        lambda self, symbol, now: (_ for _ in ()).throw(StaleMarketData("4h unavailable")),
    )
    monkeypatch.setattr("intraday.__main__._new_asset_market_caches",
                        lambda config: ({}, {}))
    monkeypatch.setattr(sys, "argv", [
        "aigt", "portfolio", "paper", "run", "--once", "--database", str(database),
    ])

    main()

    result = json.loads(capsys.readouterr().out)
    assert result["decision_scopes"] == ["perp_intraday"]
    assert store.load_parent_portfolio_state().spot_quantity == 0
    with sqlite3.connect(database) as connection:
        jobs = {row[0] for row in connection.execute("SELECT job_name FROM scheduler_runs")}
    assert "paper_spot_daily" not in jobs
