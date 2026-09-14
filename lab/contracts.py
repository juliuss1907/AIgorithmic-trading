"""Typed contracts shared by the CLI, worker, and future web application."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DataSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end_exclusive: date

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.start >= self.end_exclusive:
            raise ValueError("data.start must be before data.end_exclusive")
        return self


class SmaStrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal["sma_crossover"] = "sma_crossover"
    fast_window: int = Field(default=20, ge=1, le=500)
    slow_window: int = Field(default=50, ge=2, le=500)

    @model_validator(mode="after")
    def fast_is_shorter(self):
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        return self

    @property
    def label(self) -> str:
        return f"SMA {self.fast_window}/{self.slow_window}"


class ExperimentSpec(BaseModel):
    """User-visible research question and all assumptions needed to run it."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=1000)
    symbol: str
    data: DataSpec
    strategy: SmaStrategySpec
    initial_cash: float = Field(gt=0, allow_inf_nan=False)
    slippage_bps: tuple[float, ...] = (0, 5, 10)
    commission: Literal[0.0] = 0.0
    periods: dict[str, tuple[date, date]]

    @field_validator("title", "hypothesis")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("symbol")
    @classmethod
    def supported_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if symbol not in {"SPY", "QQQ"}:
            raise ValueError("Release 0.1 supports SPY and QQQ")
        return symbol

    @field_validator("slippage_bps")
    @classmethod
    def valid_cost_scenarios(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if not values or len(set(values)) != len(values):
            raise ValueError("slippage_bps must contain distinct scenarios")
        if any(not 0 <= value < 100 for value in values):
            raise ValueError("slippage_bps values must be in [0, 100)")
        return values

    @model_validator(mode="after")
    def valid_periods(self):
        if not self.periods:
            raise ValueError("at least one evaluation period is required")
        windows = []
        for name, (start, end) in self.periods.items():
            if not name.strip() or not self.data.start < start <= end < self.data.end_exclusive:
                raise ValueError(f"invalid evaluation period: {name!r}")
            windows.append((start, end))
        ordered = sorted(windows)
        if any(left[1] >= right[0] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("evaluation periods must not overlap")
        return self

    @property
    def engine_symbol(self) -> str:
        return f"{self.symbol}.US"

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_equity: float
    total_return: float
    cagr_252: float
    max_drawdown: float
    round_trips: int
    fills: int
    time_in_market: float
    mean_holding_sessions: float
    sessions: int
    start: date
    end: date
