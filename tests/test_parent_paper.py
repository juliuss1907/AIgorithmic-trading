from datetime import datetime, timezone

import pytest

from intraday.contracts import DecisionScope
from intraday.parent_paper import apply_paper_target
from intraday.portfolio_coordinator import (
    ParentPortfolioCoordinator,
    ParentPortfolioState,
)


NOW = datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc)


def state(**updates):
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


def test_parent_paper_ledger_can_hold_spot_long_and_perp_short_separately():
    coordinator = ParentPortfolioCoordinator()
    current = state()
    spot_auth = coordinator.authorize_target(
        current, scope=DecisionScope.SPOT_DAILY, target_notional=3_000
    )
    current, spot_fill = apply_paper_target(
        current, spot_auth, bid=99_990, ask=100_010, now=NOW
    )
    perp_auth = coordinator.authorize_target(
        current,
        scope=DecisionScope.PERP_INTRADAY,
        target_notional=-(current.equity * 0.50 - current.spot_notional),
    )
    current, perp_fill = apply_paper_target(
        current, perp_auth, bid=99_990, ask=100_010, now=NOW
    )

    assert current.spot_quantity > 0
    assert current.perp_quantity < 0
    assert spot_fill.scope == DecisionScope.SPOT_DAILY
    assert perp_fill.scope == DecisionScope.PERP_INTRADAY
    assert spot_fill.side == "buy"
    assert perp_fill.side == "sell"
    assert current.fees > 0


def test_parent_paper_target_quantity_uses_the_price_for_each_scope():
    current = state(
        mark_price=100,
        spot_price=50,
        perp_mark_price=100,
    )
    coordinator = ParentPortfolioCoordinator()
    spot_auth = coordinator.authorize_target(
        current, scope=DecisionScope.SPOT_DAILY, target_notional=1_000
    )
    current, _ = apply_paper_target(
        current, spot_auth, bid=49.9, ask=50.1, now=NOW
    )
    perp_auth = coordinator.authorize_target(
        current, scope=DecisionScope.PERP_INTRADAY, target_notional=1_000
    )
    current, _ = apply_paper_target(
        current, perp_auth, bid=99.9, ask=100.1, now=NOW
    )

    assert current.spot_quantity == pytest.approx(20)
    assert current.perp_quantity == pytest.approx(10)
    assert current.spot_price == pytest.approx(50)
    assert current.perp_mark_price == pytest.approx(100)


def test_parent_paper_deterministic_reduction_realizes_pnl():
    current = state(
        mark_price=110_000,
        spot_quantity=0.02,
        spot_entry_price=100_000,
    )
    authorization = ParentPortfolioCoordinator().authorize_target(
        current, scope=DecisionScope.SPOT_DAILY, target_notional=0
    )

    flattened, fill = apply_paper_target(
        current, authorization, bid=109_990, ask=110_010, now=NOW
    )

    assert flattened.spot_quantity == 0
    assert flattened.spot_entry_price is None
    assert flattened.realized_pnl > 190
    assert fill.reduce_only is True


def test_parent_paper_refuses_unauthorized_or_same_tick_flip():
    current = state(perp_quantity=0.01, perp_entry_price=100_000)
    denied = ParentPortfolioCoordinator().authorize_target(
        current, scope=DecisionScope.PERP_INTRADAY, target_notional=5_000
    )

    with pytest.raises(ValueError, match="authorized"):
        apply_paper_target(current, denied, bid=99_990, ask=100_010, now=NOW)
