"""Single paper ledger for parent-attributed spot and perpetual fills."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from intraday.contracts import DecisionScope
from intraday.portfolio_coordinator import (
    ParentPortfolioState,
    PortfolioAuthorization,
)


class ParentPaperFill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fill_id: str = Field(min_length=16, max_length=64)
    scope: DecisionScope
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    notional: float = Field(gt=0)
    fee: float = Field(ge=0)
    reduce_only: bool
    filled_at: datetime

    @field_validator("filled_at")
    @classmethod
    def filled_at_is_aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fill time must be timezone-aware")
        return value


def apply_paper_target(
    state: ParentPortfolioState,
    authorization: PortfolioAuthorization,
    *,
    bid: float,
    ask: float,
    now: datetime,
    fee_bps: float | None = None,
    slippage_bps: float = 5,
) -> tuple[ParentPortfolioState, ParentPaperFill | None]:
    if not authorization.allowed:
        raise ValueError("paper target must be authorized by the parent gate")
    scope = authorization.scope
    current_quantity = (
        state.spot_quantity
        if scope == DecisionScope.SPOT_DAILY
        else state.perp_quantity
    )
    current_entry = (
        state.spot_entry_price
        if scope == DecisionScope.SPOT_DAILY
        else state.perp_entry_price
    )
    target = authorization.target_notional
    if current_quantity and target and (current_quantity > 0) != (target > 0):
        raise ValueError("paper ledger refuses a same-tick position flip")
    reference_price = (
        state.spot_price
        if scope == DecisionScope.SPOT_DAILY
        else state.perp_mark_price
    )
    target_quantity = target / reference_price
    delta = target_quantity - current_quantity
    if abs(delta) < 1e-12:
        return state, None
    side = "buy" if delta > 0 else "sell"
    reference = ask if side == "buy" else bid
    slippage = slippage_bps / 10_000
    price = reference * (1 + slippage if side == "buy" else 1 - slippage)
    quantity = abs(delta)
    notional = quantity * price
    fee_rate = (10 if scope == DecisionScope.SPOT_DAILY else 5) / 10_000
    if fee_bps is not None:
        fee_rate = fee_bps / 10_000
    fee = notional * fee_rate
    reducing = abs(target_quantity) < abs(current_quantity)
    realized = state.realized_pnl
    entry_price = current_entry
    if reducing:
        realized += quantity * (price - current_entry) * (
            1 if current_quantity > 0 else -1
        )
        if target_quantity == 0:
            entry_price = None
    else:
        old_abs = abs(current_quantity)
        entry_price = (
            price
            if old_abs == 0
            else (old_abs * current_entry + quantity * price) / (old_abs + quantity)
        )
    updates = {
        "realized_pnl": realized,
        "fees": state.fees + fee,
        "updated_at": now,
    }
    if scope == DecisionScope.SPOT_DAILY:
        updates.update(
            {
                "spot_quantity": target_quantity,
                "spot_entry_price": entry_price,
                "spot_price": (bid + ask) / 2,
            }
        )
    else:
        updates.update(
            {
                "perp_quantity": target_quantity,
                "perp_entry_price": entry_price,
                "perp_mark_price": (bid + ask) / 2,
                "mark_price": (bid + ask) / 2,
            }
        )
    payload = state.model_dump()
    payload.update(updates)
    next_state = ParentPortfolioState.model_validate(payload)
    next_state = next_state.model_copy(
        update={"high_water_mark": max(state.high_water_mark, next_state.equity)}
    )
    identity = hashlib.sha256(
        f"{scope.value}:{now.isoformat()}:{target}:{price}:{quantity}".encode()
    ).hexdigest()[:32]
    fill = ParentPaperFill(
        fill_id=identity,
        scope=scope,
        side=side,
        quantity=quantity,
        price=price,
        notional=notional,
        fee=fee,
        reduce_only=reducing,
        filled_at=now,
    )
    return next_state, fill
