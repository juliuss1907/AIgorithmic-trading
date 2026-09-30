"""Small execution interface: immutable intent, confirmed state, explicit unknown."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*")
    @classmethod
    def aware_times(cls, value):
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("execution timestamps must be timezone-aware")
        return value


class AccountRef(ExecutionModel):
    venue: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,30}$")
    environment: Literal["paper", "demo"]
    account_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.environment}:{self.account_id}"


class Quote(ExecutionModel):
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    mark: Decimal = Field(gt=0)
    observed_at: datetime

    @model_validator(mode="after")
    def ordered_book(self):
        if self.bid > self.ask:
            raise ValueError("crossed execution quote")
        return self


class OrderIntent(ExecutionModel):
    intent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,36}$")
    account: AccountRef
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,24}$")
    market: Literal["spot", "perp"]
    side: Literal["BUY", "SELL"]
    quantity: Decimal = Field(gt=0)
    order_type: Literal["MARKET", "STOP_MARKET"] = "MARKET"
    reduce_only: bool = False
    stop_price: Decimal | None = Field(default=None, gt=0)
    source_id: str | None = Field(default=None, max_length=128)
    created_at: datetime

    @model_validator(mode="after")
    def protection_is_reducing(self):
        if self.order_type == "STOP_MARKET":
            if self.market != "perp" or not self.reduce_only or self.stop_price is None:
                raise ValueError("protective stop requires Perp, reduce-only, and trigger")
        elif self.stop_price is not None:
            raise ValueError("market orders cannot have a stop trigger")
        return self


class ExecutionFill(ExecutionModel):
    fill_id: str = Field(min_length=1, max_length=128)
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    commission: Decimal = Field(ge=0)
    commission_asset: str = Field(min_length=1, max_length=24)
    filled_at: datetime


class OrderUpdate(ExecutionModel):
    intent_id: str
    status: Literal["UNKNOWN", "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"]
    exchange_order_id: str | None = None
    executed_quantity: Decimal = Field(default=Decimal(0), ge=0)
    average_price: Decimal | None = Field(default=None, gt=0)
    fills: tuple[ExecutionFill, ...] = ()
    received_at: datetime

    @property
    def terminal(self) -> bool:
        return self.status in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


class Position(ExecutionModel):
    symbol: str
    quantity: Decimal
    entry_price: Decimal = Field(ge=0)
    mark_price: Decimal = Field(gt=0)


class OpenOrder(ExecutionModel):
    symbol: str
    client_id: str
    order_type: str


class AccountSnapshot(ExecutionModel):
    account: AccountRef
    wallet_balance: Decimal = Field(ge=0)
    equity: Decimal = Field(gt=0)
    available_balance: Decimal = Field(ge=0)
    positions: tuple[Position, ...] = ()
    open_orders: tuple[OpenOrder, ...] = ()
    can_trade: bool
    one_way: bool
    single_asset: bool
    margin_mode: str
    leverage: int = Field(ge=1)
    observed_at: datetime

    def position(self, symbol: str) -> Position | None:
        return next((p for p in self.positions if p.symbol == symbol and p.quantity), None)


class InstrumentRules(ExecutionModel):
    symbol: str
    quantity_step: Decimal = Field(gt=0)
    min_quantity: Decimal = Field(ge=0)
    max_quantity: Decimal = Field(gt=0)
    min_notional: Decimal = Field(ge=0)
    price_tick: Decimal = Field(gt=0)

    def round_quantity(self, quantity: Decimal) -> Decimal:
        return (quantity / self.quantity_step).to_integral_value(rounding=ROUND_DOWN) * self.quantity_step

    def validate_quantity(self, quantity: Decimal, price: Decimal, *, reducing=False):
        if quantity <= 0 or quantity != self.round_quantity(quantity):
            raise ValueError("quantity does not match instrument step")
        if not self.min_quantity <= quantity <= self.max_quantity:
            raise ValueError("quantity outside instrument limits")
        if not reducing and quantity * price < self.min_notional:
            raise ValueError("order below minimum notional")


class ExecutionAdapter(Protocol):
    account_ref: AccountRef

    def submit(self, intent: OrderIntent) -> OrderUpdate: ...
    def query(self, intent: OrderIntent) -> OrderUpdate | None: ...
    def cancel(self, intent: OrderIntent) -> OrderUpdate: ...


class TradingVenue(ExecutionAdapter, Protocol):
    def account_snapshot(self, *, now: datetime) -> AccountSnapshot: ...
    def quote(self, symbol: str, *, now: datetime) -> Quote: ...
    def instrument(self, symbol: str) -> InstrumentRules: ...


class ExecutionUnavailable(RuntimeError):
    """Sanitized transport/protocol failure; caller must reconcile mutations."""
