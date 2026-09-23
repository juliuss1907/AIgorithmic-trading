import json
import sqlite3
from datetime import datetime, timedelta, timezone

from intraday.contracts import (
    DecisionScope,
    Direction,
    FeatureSnapshot,
    JevDecision,
    JevDecisionTrace,
    PerpRuleParameters,
    Regime,
    RiskLevel,
    ScopedJevDecision,
    SpotRuleParameters,
)
from intraday.parent_runtime import (
    flatten_parent_paper_positions,
    run_parent_paper_cycle,
)
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.spot_signal import DonchianObservation
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 23, 5, 0, tzinfo=timezone.utc)


def snapshot(price=100_000):
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=NOW,
        built_at=NOW,
        bid=price - 10,
        ask=price + 10,
        features={"price": price, "mark_price": price},
        freshness={"candles": True, "book": True},
    )


def observation(*, entry=True, exit=False):
    return DonchianObservation(
        close=100_000,
        entry_channel=99_000,
        exit_channel=90_000,
        atr=2_000,
        atr_pct=0.02,
        size_multiplier=1,
        entry=entry,
        exit=exit,
    )


class Provider:
    def __init__(self, *, perp_direction=Direction.SELL, confidence=0.91):
        self.perp_direction = perp_direction
        self.confidence = confidence

    def decide_scoped(self, market, tick_id, scope, now):
        direction = (
            Direction.BUY
            if scope == DecisionScope.SPOT_DAILY
            else self.perp_direction
        )
        decision = JevDecision(
            decision_id=f"decision-{tick_id}",
            tick_id=tick_id,
            snapshot_id=market.snapshot_id,
            direction=direction,
            direction_confidence=self.confidence,
            regime=Regime.TRENDING_UP,
            toxic_flow=0.1,
            entry_quality=4,
            risk_level=RiskLevel.LOW,
            model_ref="fake/jev",
            created_at=now,
        )
        return ScopedJevDecision(
            scope=scope,
            workflow=(
                "spot_daily_entry"
                if scope == DecisionScope.SPOT_DAILY
                else "perp_intraday_entry"
            ),
            decision=decision,
            trace=JevDecisionTrace(
                state_snapshot=json.dumps(
                    {
                        "decision_scope": scope.value,
                        "features": market.features,
                        "symbol": market.symbol,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                raw_signals={
                    **market.features,
                    "bid": market.bid,
                    "ask": market.ask,
                },
                jev_answers={
                    "direction": {
                        "choice": direction.value,
                        "probabilities": {direction.value: self.confidence},
                    }
                },
            ),
        )


def active_state(**updates):
    values = {
        "mark_price": 100_000,
        "day_start_equity": 10_000,
        "high_water_mark": 10_000,
        "entries_paused": False,
        "paper_active": True,
        "updated_at": NOW,
    }
    values.update(updates)
    return ParentPortfolioState(**values)


def test_parent_cycle_opens_attributed_spot_and_perp_paper_positions(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.save_parent_portfolio_state(
        active_state(), event_kind="activated", actor="test"
    )

    result = run_parent_paper_cycle(
        store,
        Provider(),
        snapshot(),
        observation(),
        spot_rule=SpotRuleParameters(),
        perp_rule=PerpRuleParameters(),
        now=NOW,
    )

    state = store.load_parent_portfolio_state()
    assert result["fills"] == 2
    assert state.spot_quantity > 0
    assert state.perp_quantity < 0
    assert len(store.list_parent_paper_fills()) == 2
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM open_trade_context"
        ).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_parent_cycle_closes_both_scopes_into_immutable_completed_trades(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.save_parent_portfolio_state(
        active_state(), event_kind="activated", actor="test"
    )
    run_parent_paper_cycle(
        store,
        Provider(),
        snapshot(),
        observation(),
        spot_rule=SpotRuleParameters(),
        perp_rule=PerpRuleParameters(),
        now=NOW,
    )

    closed_at = NOW + timedelta(minutes=30)
    result = run_parent_paper_cycle(
        store,
        Provider(perp_direction=Direction.TAKE_PROFIT),
        snapshot(price=99_000),
        observation(entry=False, exit=True),
        spot_rule=SpotRuleParameters(),
        perp_rule=PerpRuleParameters(),
        now=closed_at,
    )

    assert result["fills"] == 2
    with sqlite3.connect(store.database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM trades ORDER BY scope").fetchall()
        open_count = connection.execute(
            "SELECT COUNT(*) FROM open_trade_context"
        ).fetchone()[0]
    assert open_count == 0
    assert {row["scope"] for row in rows} == {"spot_daily", "perp_intraday"}
    assert {row["close_reason"] for row in rows} == {
        "donchian_exit",
        "take_profit",
    }
    assert all(row["duration_sec"] == 1800 for row in rows)
    assert all(row["is_paper"] == 1 for row in rows)


def test_rejected_scoped_decisions_are_journaled_without_opening_trades(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.save_parent_portfolio_state(
        active_state(), event_kind="activated", actor="test"
    )

    result = run_parent_paper_cycle(
        store,
        Provider(confidence=0.5),
        snapshot(),
        observation(),
        spot_rule=SpotRuleParameters(),
        perp_rule=PerpRuleParameters(),
        now=NOW,
    )

    assert result["fills"] == 0
    with sqlite3.connect(store.database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM signals ORDER BY scope").fetchall()
    assert len(rows) == 2
    assert all(row["gate_passed"] == 0 for row in rows)
    assert all(row["gate_reason"] == "low_confidence" for row in rows)


def test_manual_flatten_closes_open_journal_trades(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.save_parent_portfolio_state(
        active_state(), event_kind="activated", actor="test"
    )
    run_parent_paper_cycle(
        store,
        Provider(),
        snapshot(),
        observation(),
        spot_rule=SpotRuleParameters(),
        perp_rule=PerpRuleParameters(),
        now=NOW,
    )

    flattened = flatten_parent_paper_positions(
        store,
        store.load_parent_portfolio_state(),
        now=NOW + timedelta(minutes=5),
        bid=99_990,
        ask=100_010,
        actor="test",
    )

    assert flattened.spot_quantity == 0
    assert flattened.perp_quantity == 0
    assert flattened.entries_paused is True
    with sqlite3.connect(store.database) as connection:
        reasons = {
            row[0] for row in connection.execute("SELECT close_reason FROM trades")
        }
    assert reasons == {"manual"}


def test_deterministic_spot_exit_runs_even_when_provider_is_down(tmp_path):
    class DownProvider:
        def decide_scoped(self, *args, **kwargs):
            raise RuntimeError("provider_down")

    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.save_parent_portfolio_state(
        active_state(spot_quantity=0.02, spot_entry_price=90_000),
        event_kind="seed",
        actor="test",
    )

    result = run_parent_paper_cycle(
        store,
        DownProvider(),
        snapshot(),
        observation(entry=False, exit=True),
        spot_rule=SpotRuleParameters(),
        perp_rule=PerpRuleParameters(),
        now=NOW,
    )

    assert result["fills"] == 1
    assert result["provider_errors"] == ["perp_intraday"]
    assert store.load_parent_portfolio_state().spot_quantity == 0
