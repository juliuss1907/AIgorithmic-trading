"""Frozen replay inputs and explicit, offline execution assumptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from intraday.assets import ticker_symbol
from intraday.contracts import DecisionScope, JevDecision, ScopedRuleCandidate
from intraday.execution.contracts import InstrumentRules


WIDTH_MS = 14_400_000


def utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("replay timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def positive_policy_number(value: Decimal) -> Decimal:
    # Existing pure coordinator/Donchian helpers use float; reject overflow and
    # underflow at the boundary, before they can corrupt a research calculation.
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError("value must fit finite positive policy arithmetic")
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FundingSettlement(FrozenModel):
    at: datetime
    rate: Decimal = Field(ge=-1, le=1)
    mark: Decimal = Field(gt=0)

    _at = field_validator("at")(utc)
    _mark = field_validator("mark")(positive_policy_number)


class FundingHistory(FrozenModel):
    """Declared settlement coverage, not lastFundingRate or an estimated schedule."""

    symbol: str
    source: str = Field(min_length=1, max_length=200)
    coverage_start: datetime
    coverage_end: datetime
    settlements: tuple[FundingSettlement, ...] = ()

    _symbol = field_validator("symbol")(ticker_symbol)
    _times = field_validator("coverage_start", "coverage_end")(utc)

    @model_validator(mode="after")
    def valid_coverage(self):
        times = [row.at for row in self.settlements]
        if self.coverage_end <= self.coverage_start or times != sorted(set(times)):
            raise ValueError("funding coverage must be ordered and settlements unique")
        if any(not self.coverage_start <= at < self.coverage_end for at in times):
            raise ValueError("funding settlement outside declared coverage")
        return self


class ExecutionProfile(FrozenModel):
    profile_id: Literal["bnb-demo"] = "bnb-demo"
    version: Literal["1"] = "1"
    spot_cost_bps: Decimal = Field(default=Decimal(15), ge=0, le=1000)
    perp_cost_bps: Decimal = Field(default=Decimal(5), ge=0, le=1000)
    instrument: InstrumentRules | None = None
    instrument_observed_at: datetime | None = None
    instrument_source: str | None = Field(default=None, min_length=1, max_length=200)
    funding: FundingHistory | None = None

    @field_validator("instrument_observed_at")
    @classmethod
    def aware_metadata(cls, value):
        return utc(value) if value is not None else None

    @model_validator(mode="after")
    def explicit_metadata(self):
        if self.instrument is not None and (self.instrument_observed_at is None or not self.instrument_source):
            raise ValueError("instrument rules require source and observation timestamp")
        return self

    def cost_bps(self, market: str) -> Decimal:
        return self.spot_cost_bps if market == "spot" else self.perp_cost_bps


class ReplayConfig(FrozenModel):
    symbol: str
    market: Literal["spot", "perp"]
    rule_id: str = Field(min_length=1, max_length=128)
    start: datetime
    end: datetime
    capital: Decimal = Field(default=Decimal(1000), gt=0, le=10000)
    leverage: int = Field(default=3, ge=1, le=10, strict=True)
    profile: ExecutionProfile = Field(default_factory=ExecutionProfile)

    _symbol = field_validator("symbol")(ticker_symbol)
    _times = field_validator("start", "end")(utc)
    _capital = field_validator("capital")(positive_policy_number)

    @model_validator(mode="after")
    def valid_window_and_identity(self):
        if self.end <= self.start:
            raise ValueError("replay end must follow start")
        if self.profile.instrument and self.profile.instrument.symbol != self.symbol:
            raise ValueError("instrument rules belong to another symbol")
        if self.profile.funding and (self.market != "perp" or self.profile.funding.symbol != self.symbol):
            raise ValueError("funding history belongs to another symbol or market")
        return self

    @property
    def scope(self) -> DecisionScope:
        return DecisionScope.SPOT_4H if self.market == "spot" else DecisionScope.PERP_INTRADAY


class Candle(FrozenModel):
    opened_at: datetime
    available_at: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)

    _times = field_validator("opened_at", "available_at")(utc)
    _prices = field_validator("open", "high", "low", "close")(positive_policy_number)

    @model_validator(mode="after")
    def valid_bar(self):
        if self.available_at - self.opened_at != timedelta(hours=4):
            raise ValueError("expected native 4h candle")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("invalid OHLC bounds")
        return self

    @classmethod
    def from_row(cls, row) -> "Candle":
        if len(row) < 7 or isinstance(row[0], bool) or isinstance(row[6], bool):
            raise ValueError("invalid candle row")
        opening, closing = int(row[0]), int(row[6])
        if opening != row[0] or closing != row[6] or opening % WIDTH_MS or closing != opening + WIDTH_MS - 1:
            raise ValueError("invalid native candle interval")
        return cls(opened_at=datetime.fromtimestamp(opening / 1000, timezone.utc),
                   available_at=datetime.fromtimestamp((closing + 1) / 1000, timezone.utc),
                   open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])

    def row(self) -> list:
        opening = int(self.opened_at.timestamp() * 1000)
        return [opening, str(self.open), str(self.high), str(self.low), str(self.close),
                str(self.volume), opening + WIDTH_MS - 1]


class QuotePoint(FrozenModel):
    at: datetime
    event_time: datetime
    snapshot_id: str
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    mark: Decimal = Field(gt=0)
    fresh: bool = True

    _times = field_validator("at", "event_time")(utc)
    _prices = field_validator("bid", "ask", "mark")(positive_policy_number)

    @model_validator(mode="after")
    def valid_quote(self):
        if self.bid > self.ask or self.at < self.event_time:
            raise ValueError("invalid quote spread or availability")
        return self


@dataclass(frozen=True)
class RecordedDecision:
    decision: JevDecision
    available_at: datetime
    reference_price: Decimal
    features_valid: bool
    provenance_id: str


@dataclass(frozen=True)
class ReplayDataset:
    rule: ScopedRuleCandidate
    candles: tuple[Candle, ...] = ()
    quotes: tuple[QuotePoint, ...] = ()
    decisions: tuple[RecordedDecision, ...] = ()
    limitations: tuple[str, ...] = ()
    v1_reference: dict | None = None
