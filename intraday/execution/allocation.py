"""Operator-owned capital allocation; no implicit redistribution or wallet adoption."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from intraday.assets import normalize_symbol
from intraday.execution.contracts import ExecutionModel


class DemoAllocation(ExecutionModel):
    capital: Decimal = Field(gt=0, le=10000)
    spot_emergency_stop_pct: Decimal = Field(default=Decimal(".10"), ge=Decimal(".0075"), le=Decimal(".10"))
    spot_weights: dict[str, Decimal] = Field(default_factory=dict)
    perp_weights: dict[str, Decimal] = Field(default_factory=dict)
    # Setup-2 profile (ADR-006): sleeves follow the operator split and the bound active-set version.
    profile: Literal["legacy", "setup2_v1"] = "legacy"
    spot_split: Decimal | None = Field(default=None, ge=0, le=1)
    perp_split: Decimal | None = Field(default=None, ge=0, le=1)
    active_set_version_id: str | None = None

    @model_serializer(mode="wrap")
    def serialize_allocation(self, handler):
        payload = handler(self)
        if self.profile == "legacy":
            for key in ("profile", "spot_split", "perp_split", "active_set_version_id"):
                payload.pop(key, None)
        return payload

    @field_validator("spot_weights", "perp_weights")
    @classmethod
    def manual_weights(cls, weights):
        if any(normalize_symbol(symbol) != symbol or not weight.is_finite() or not 0 <= weight <= 1
               for symbol, weight in weights.items()):
            raise ValueError("weights require canonical USDT symbols and fractions from zero to one")
        if sum(weights.values(), Decimal(0)) > 1:
            raise ValueError("coin weights must total at most one per sleeve")
        return weights

    @model_validator(mode="after")
    def nonempty_allocation(self):
        if not any(self.spot_weights.values()) and not any(self.perp_weights.values()):
            raise ValueError("allocate at least one coin explicitly")
        setup2 = (self.spot_split, self.perp_split, self.active_set_version_id)
        if self.profile == "legacy" and any(v is not None for v in setup2):
            raise ValueError("splits and active-set binding require the setup2 profile")
        if self.profile == "setup2_v1" and (any(v is None for v in setup2) or self.spot_split + self.perp_split > 1):
            raise ValueError("setup2 allocation requires splits totalling at most one and an active-set version")
        return self

    def split(self, market):
        if self.profile == "legacy":
            return Decimal(".30") if market == "spot" else Decimal(".20")
        return self.spot_split if market == "spot" else self.perp_split

    def target_cap(self, symbol, market):
        weights = self.spot_weights if market == "spot" else self.perp_weights if market == "perp" else None
        if weights is None or symbol not in weights or weights[symbol] <= 0:
            raise ValueError("route has no positive operator weight")
        return self.capital * self.split(market) * weights[symbol]

    def routes(self):
        return [(symbol, market) for market, weights in (("spot", self.spot_weights), ("perp",self.perp_weights))
                for symbol,weight in weights.items() if weight > 0]
