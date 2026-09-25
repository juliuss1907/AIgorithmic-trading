import json
import sys
from datetime import datetime, timezone

from intraday.contracts import DecisionScope
from intraday.__main__ import main
from intraday.portfolio_coordinator import (
    ParentPortfolioCoordinator,
    ParentPortfolioState,
)
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc)


def state(**values):
    defaults = {
        "initial_equity": 10_000,
        "realized_pnl": 0,
        "fees": 0,
        "funding": 0,
        "spot_quantity": 0,
        "spot_entry_price": None,
        "perp_quantity": 0,
        "perp_entry_price": None,
        "mark_price": 100_000,
        "day_start_equity": 10_000,
        "high_water_mark": 10_000,
        "entries_paused": False,
        "halt_reason": None,
        "paper_active": True,
        "updated_at": NOW,
    }
    defaults.update(values)
    return ParentPortfolioState(**defaults)


def test_parent_budget_allows_maximum_spot_and_perp_sleeves_together():
    coordinator = ParentPortfolioCoordinator()
    current = state()

    spot = coordinator.authorize_target(
        current, scope=DecisionScope.SPOT_DAILY, target_notional=3_000
    )
    with_spot = current.model_copy(update={"spot_quantity": 0.03, "spot_entry_price": 100_000})
    perp = coordinator.authorize_target(
        with_spot, scope=DecisionScope.PERP_INTRADAY, target_notional=-2_000
    )

    assert spot.allowed is True
    assert perp.allowed is True
    assert perp.projected_gross_exposure_pct == 0.5
    assert perp.projected_net_delta_pct == 0.1
    assert round(perp.projected_isolated_margin_pct, 6) == round(2_000 / 3 / 10_000, 6)


def test_parent_valuation_uses_scope_specific_prices():
    current = state(
        mark_price=120_000,
        spot_price=100_000,
        perp_mark_price=120_000,
        spot_quantity=0.02,
        spot_entry_price=90_000,
        perp_quantity=-0.01,
        perp_entry_price=110_000,
    )

    assert current.spot_notional == 2_000
    assert current.perp_notional == -1_200
    assert current.equity == 10_100


def test_legacy_parent_state_seeds_both_scope_prices_from_mark_price():
    current = state()
    payload = current.model_dump(exclude={"spot_price", "perp_mark_price"})

    restored = ParentPortfolioState.model_validate(payload)

    assert restored.spot_price == 100_000
    assert restored.perp_mark_price == 100_000
    assert restored.mark_price == restored.perp_mark_price


def test_parent_budget_rejects_sleeve_and_gross_limit_breaches():
    coordinator = ParentPortfolioCoordinator()

    spot = coordinator.authorize_target(
        state(), scope=DecisionScope.SPOT_DAILY, target_notional=3_001
    )
    crowded = state(
        spot_quantity=0.03,
        spot_entry_price=100_000,
        perp_quantity=0.02,
        perp_entry_price=100_000,
    )
    gross = coordinator.authorize_target(
        crowded, scope=DecisionScope.PERP_INTRADAY, target_notional=2_001
    )

    assert spot.allowed is False
    assert "spot_sleeve_limit" in spot.reason_codes
    assert gross.allowed is False
    assert "perp_sleeve_limit" in gross.reason_codes
    assert "gross_exposure_limit" in gross.reason_codes


def test_daily_loss_blocks_entries_but_never_blocks_reduction():
    coordinator = ParentPortfolioCoordinator()
    current = state(
        realized_pnl=-160,
        spot_quantity=0.01,
        spot_entry_price=100_000,
    )

    add = coordinator.authorize_target(
        current, scope=DecisionScope.SPOT_DAILY, target_notional=2_000
    )
    exit_ = coordinator.authorize_target(
        current, scope=DecisionScope.SPOT_DAILY, target_notional=0
    )

    assert add.allowed is False
    assert "daily_loss_limit" in add.reason_codes
    assert exit_.allowed is True
    assert exit_.reduce_only is True


def test_drawdown_requires_parent_flatten_and_perp_cannot_flip_same_tick():
    coordinator = ParentPortfolioCoordinator()
    drawdown = state(
        realized_pnl=-900,
        high_water_mark=10_000,
        perp_quantity=0.01,
        perp_entry_price=100_000,
    )
    halted = coordinator.authorize_target(
        drawdown, scope=DecisionScope.PERP_INTRADAY, target_notional=1_000
    )
    healthy = state(perp_quantity=0.01, perp_entry_price=100_000)
    flip = coordinator.authorize_target(
        healthy, scope=DecisionScope.PERP_INTRADAY, target_notional=-1_000
    )

    assert halted.allowed is False
    assert halted.flatten_required is True
    assert "parent_drawdown_limit" in halted.reason_codes
    assert flip.allowed is True
    assert flip.reduce_only is True
    assert flip.target_notional == 0
    assert flip.reason_codes == ("no_same_tick_flip",)


def test_portfolio_cli_status_pause_resume_and_flatten(monkeypatch, capsys, tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    current = state(
        spot_quantity=0.02,
        spot_entry_price=90_000,
        perp_quantity=-0.01,
        perp_entry_price=110_000,
        high_water_mark=10_300,
    )
    store.save_parent_portfolio_state(current, event_kind="initialized", actor="test")

    def invoke(*arguments):
        monkeypatch.setattr(
            sys,
            "argv",
            ["aigt", "portfolio", *arguments, "--database", str(database)],
        )
        main()
        return json.loads(capsys.readouterr().out)

    status = invoke("status")
    paused = invoke("pause")
    resumed = invoke("resume")
    flattened = invoke("flatten")

    assert status["mode"] == "paper"
    assert status["spot_notional"] == 2_000
    assert status["perp_notional"] == -1_000
    assert paused["entries_paused"] is True
    assert resumed["entries_paused"] is False
    assert flattened["spot_notional"] == 0
    assert flattened["perp_notional"] == 0
    assert flattened["entries_paused"] is True
    assert len(store.list_parent_portfolio_events()) == 4
