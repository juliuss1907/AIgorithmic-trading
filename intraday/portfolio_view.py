"""Read-time market references for the combined parent portfolio."""

from __future__ import annotations

from intraday.portfolio_coordinator import ParentPortfolioState


def _reference(snapshot, *keys: str) -> float | None:
    if snapshot is None:
        return None
    for key in keys:
        value = snapshot.features.get(key)
        if value is not None:
            return float(value)
    return None


def latest_parent_market_view(store, state: ParentPortfolioState) -> ParentPortfolioState:
    """Mark a read model from the latest scoped snapshots without persisting it."""
    perp_snapshot = store.latest_snapshot(market="binance_usdm_perp")
    spot_snapshot = store.latest_snapshot(market="binance_spot")
    perp_reference = _reference(
        perp_snapshot, "reference_price", "mark_price", "price"
    )
    spot_reference = _reference(spot_snapshot, "reference_price", "price")
    payload = state.model_dump()
    if perp_reference is not None:
        payload["mark_price"] = perp_reference
        payload["perp_mark_price"] = perp_reference
    if spot_reference is not None:
        payload["spot_price"] = spot_reference
    return ParentPortfolioState.model_validate(payload)
