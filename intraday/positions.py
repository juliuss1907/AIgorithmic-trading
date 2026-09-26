"""Read-only projection of open parent portfolio positions."""

from __future__ import annotations

from datetime import datetime, timezone

from intraday.contracts import DecisionScope
from intraday.portfolio_view import latest_parent_market_view


POSITIONS_SCHEMA_VERSION = "1"


def _position(
    *,
    scope: DecisionScope,
    market: str,
    quantity: float,
    entry_price: float,
    mark_price: float,
    mark_event_time: datetime,
) -> dict:
    entry_notional = abs(quantity) * entry_price
    unrealized_pnl = quantity * (mark_price - entry_price)
    return {
        "scope": scope.value,
        "symbol": "BTCUSDT",
        "market": market,
        "side": "long" if quantity > 0 else "short",
        "quantity": quantity,
        "entry_price": entry_price,
        "mark_price": mark_price,
        "mark_event_time": mark_event_time.isoformat(),
        "notional_usd": abs(quantity) * mark_price,
        "unrealized_pnl_usd": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl / entry_notional * 100,
    }


def build_positions_snapshot(store, *, now: datetime) -> dict:
    """List currently open Spot and Perp paper positions without persisting."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("positions time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    state = store.load_parent_portfolio_state()
    if state is None:
        return {
            "positions_schema_version": POSITIONS_SCHEMA_VERSION,
            "generated_at": now.isoformat(),
            "portfolio_initialized": False,
            "paper_active": False,
            "entries_paused": True,
            "count": 0,
            "positions": [],
        }

    marked = latest_parent_market_view(store, state)
    spot_snapshot = store.latest_snapshot(market="binance_spot")
    perp_snapshot = store.latest_snapshot(market="binance_usdm_perp")
    positions = []
    if marked.spot_quantity:
        positions.append(
            _position(
                scope=DecisionScope.SPOT_DAILY,
                market="binance_spot",
                quantity=marked.spot_quantity,
                entry_price=marked.spot_entry_price,
                mark_price=marked.spot_price,
                mark_event_time=(
                    spot_snapshot.event_time if spot_snapshot else marked.updated_at
                ),
            )
        )
    if marked.perp_quantity:
        positions.append(
            _position(
                scope=DecisionScope.PERP_INTRADAY,
                market="binance_usdm_perp",
                quantity=marked.perp_quantity,
                entry_price=marked.perp_entry_price,
                mark_price=marked.perp_mark_price,
                mark_event_time=(
                    perp_snapshot.event_time if perp_snapshot else marked.updated_at
                ),
            )
        )
    return {
        "positions_schema_version": POSITIONS_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "portfolio_initialized": True,
        "paper_active": marked.paper_active,
        "entries_paused": marked.entries_paused,
        "count": len(positions),
        "positions": positions,
    }
