from datetime import datetime, timezone

from intraday.contracts import (
    DecisionScope,
    Direction,
    FeatureSnapshot,
    JevDecision,
    PerpRuleParameters,
    Regime,
    RiskLevel,
    ScopedJevDecision,
    SpotRuleParameters,
)
from intraday.parent_runtime import run_parent_paper_cycle
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
    def decide_scoped(self, market, tick_id, scope, now):
        direction = (
            Direction.BUY
            if scope == DecisionScope.SPOT_DAILY
            else Direction.SELL
        )
        decision = JevDecision(
            decision_id=f"decision-{scope.value}",
            tick_id=tick_id,
            snapshot_id=market.snapshot_id,
            direction=direction,
            direction_confidence=0.91,
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
