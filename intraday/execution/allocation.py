"""Operator-owned capital allocation; no implicit redistribution or wallet adoption."""

from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from intraday.assets import normalize_symbol
from intraday.execution.contracts import ExecutionModel


class DemoAllocation(ExecutionModel):
    capital: Decimal = Field(gt=0, le=10000)
    spot_emergency_stop_pct: Decimal = Field(default=Decimal(".10"), ge=Decimal(".0075"), le=Decimal(".10"))
    spot_weights: dict[str, Decimal] = Field(default_factory=dict)
    perp_weights: dict[str, Decimal] = Field(default_factory=dict)

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
        return self

    def target_cap(self, symbol, market):
        weights = self.spot_weights if market == "spot" else self.perp_weights if market == "perp" else None
        if weights is None or symbol not in weights or weights[symbol] <= 0:
            raise ValueError("route has no positive operator weight")
        return self.capital * (Decimal(".30") if market == "spot" else Decimal(".20")) * weights[symbol]

    def routes(self):
        return [(symbol, market) for market, weights in (("spot", self.spot_weights), ("perp",self.perp_weights))
                for symbol,weight in weights.items() if weight > 0]
