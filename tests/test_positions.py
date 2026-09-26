from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import FeatureSnapshot
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.positions import build_positions_snapshot
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)


def market_snapshot(*, market: str, price: float, at: datetime) -> FeatureSnapshot:
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        market=market,
        timeframe="1d" if market == "binance_spot" else "1h",
        feature_schema_version="2",
        event_time=at,
        built_at=at,
        bid=price - 10,
        ask=price + 10,
        features={
            "price": price - 100,
            "reference_price": price,
            **({} if market == "binance_spot" else {"mark_price": price}),
        },
        freshness={"candles": True, "order_book": True},
    )


def parent_state(**changes) -> ParentPortfolioState:
    values = {
        "mark_price": 100_000,
        "spot_price": 100_000,
        "perp_mark_price": 100_000,
        "day_start_equity": 10_000,
        "high_water_mark": 10_000,
        "entries_paused": False,
        "paper_active": True,
        "updated_at": NOW - timedelta(minutes=5),
    }
    values.update(changes)
    return ParentPortfolioState(**values)


def test_positions_snapshot_is_empty_when_portfolio_is_not_initialized(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite3")

    payload = build_positions_snapshot(store, now=NOW)

    assert payload == {
        "positions_schema_version": "1",
        "generated_at": NOW.isoformat(),
        "portfolio_initialized": False,
        "paper_active": False,
        "entries_paused": True,
        "count": 0,
        "positions": [],
    }


def test_positions_snapshot_lists_open_spot_and_short_perp_with_latest_marks(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite3")
    state = parent_state(
        spot_quantity=0.02,
        spot_entry_price=90_000,
        perp_quantity=-0.01,
        perp_entry_price=110_000,
    )
    store.save_parent_portfolio_state(state, event_kind="test", actor="test")
    spot_at = NOW - timedelta(minutes=2)
    perp_at = NOW - timedelta(seconds=20)
    store.record_snapshot(
        market_snapshot(market="binance_spot", price=100_000, at=spot_at)
    )
    store.record_snapshot(
        market_snapshot(market="binance_usdm_perp", price=120_000, at=perp_at)
    )

    payload = build_positions_snapshot(store, now=NOW)

    assert payload["portfolio_initialized"] is True
    assert payload["paper_active"] is True
    assert payload["entries_paused"] is False
    assert payload["count"] == 2
    assert payload["positions"] == [
        {
            "scope": "spot_daily",
            "symbol": "BTCUSDT",
            "market": "binance_spot",
            "side": "long",
            "quantity": 0.02,
            "entry_price": 90_000,
            "mark_price": 100_000,
            "mark_event_time": spot_at.isoformat(),
            "notional_usd": 2_000,
            "unrealized_pnl_usd": 200,
            "unrealized_pnl_pct": pytest.approx(11.1111111111),
        },
        {
            "scope": "perp_intraday",
            "symbol": "BTCUSDT",
            "market": "binance_usdm_perp",
            "side": "short",
            "quantity": -0.01,
            "entry_price": 110_000,
            "mark_price": 120_000,
            "mark_event_time": perp_at.isoformat(),
            "notional_usd": 1_200,
            "unrealized_pnl_usd": -100,
            "unrealized_pnl_pct": pytest.approx(-9.0909090909),
        },
    ]


def test_positions_snapshot_omits_closed_sleeves(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite3")
    store.save_parent_portfolio_state(
        parent_state(), event_kind="test", actor="test"
    )

    payload = build_positions_snapshot(store, now=NOW)

    assert payload["portfolio_initialized"] is True
    assert payload["count"] == 0
    assert payload["positions"] == []
