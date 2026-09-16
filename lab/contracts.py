"""Typed contracts shared by the CLI, worker, and future web application."""

from datetime import date
from typing import Annotated, Literal

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


class DatasetRequest(DataSpec):
    """A bounded market-data download; no arbitrary ticker or interval."""

    market: Literal["us_equity", "crypto_spot"] = "us_equity"
    venue: Literal["yahoo", "binance"] = "yahoo"
    symbol: str
    interval: Literal["1d"] = "1d"
    calendar: Literal["XNYS", "UTC_24_7"] = "XNYS"

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def supported_instrument(self):
        allowed = {
            ("us_equity", "yahoo", "XNYS"): {"SPY", "QQQ"},
            ("crypto_spot", "binance", "UTC_24_7"): {"BTCUSDT"},
        }
        symbols = allowed.get((self.market, self.venue, self.calendar), set())
        if self.symbol not in symbols:
            raise ValueError("Unsupported market, venue, calendar, or symbol combination")
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

    @property
    def warmup_sessions(self) -> int:
        return self.slow_window


class RsiBollingerStrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal["rsi_bollinger"] = "rsi_bollinger"
    rsi_window: int = Field(default=14, ge=2, le=100)
    bollinger_window: int = Field(default=20, ge=2, le=200)
    bollinger_stddev: float = Field(default=2, gt=0, le=10, allow_inf_nan=False)
    entry_rsi: float = Field(default=30, ge=0, le=100, allow_inf_nan=False)
    exit_rsi: float = Field(default=50, ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def thresholds_are_ordered(self):
        if self.entry_rsi >= self.exit_rsi:
            raise ValueError("entry_rsi must be smaller than exit_rsi")
        return self

    @property
    def label(self) -> str:
        return f"RSI {self.rsi_window} + Bollinger {self.bollinger_window}/{self.bollinger_stddev:g}"

    @property
    def warmup_sessions(self) -> int:
        return max(self.rsi_window, self.bollinger_window)


class DonchianStrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal["donchian_breakout"] = "donchian_breakout"
    entry_window: int = Field(default=20, ge=2, le=500)
    exit_window: int = Field(default=10, ge=2, le=500)
    atr_window: int = Field(default=14, ge=2, le=200)

    @property
    def label(self) -> str:
        return f"Donchian {self.entry_window}/{self.exit_window} + ATR {self.atr_window}"

    @property
    def warmup_sessions(self) -> int:
        return max(self.entry_window, self.exit_window, self.atr_window)


StrategySpec = SmaStrategySpec | RsiBollingerStrategySpec | DonchianStrategySpec


class FixedPositionSizingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal["fixed"] = "fixed"


class EntryVolatilityPositionSizingSpec(BaseModel):
    """Pre-registered BTC risk budget; values are intentionally not tunable."""

    model_config = ConfigDict(extra="forbid")

    family: Literal["entry_volatility"] = "entry_volatility"
    lookback: Literal[20] = 20
    annual_target: Literal[0.2] = 0.2
    annualization_days: Literal[365] = 365


PositionSizingSpec = Annotated[
    FixedPositionSizingSpec | EntryVolatilityPositionSizingSpec,
    Field(discriminator="family"),
]


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_target_weight: float = Field(default=1.0, gt=0, le=1, allow_inf_nan=False)
    halt_drawdown: float = Field(default=0.3, gt=0, lt=1, allow_inf_nan=False)
    position_sizing: PositionSizingSpec = Field(default_factory=FixedPositionSizingSpec)


class CandidateLock(BaseModel):
    """Immutable contract joining a selected gate candidate to verified run evidence."""

    model_config = ConfigDict(extra="forbid")

    candidate: Literal["donchian_breakout"]
    run_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    dataset_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    parent_run_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    strategy: DonchianStrategySpec
    position_sizing: EntryVolatilityPositionSizingSpec
    market: Literal["crypto_spot"] = "crypto_spot"
    venue: Literal["binance"] = "binance"
    symbol: Literal["BTCUSDT"] = "BTCUSDT"
    interval: Literal["1d"] = "1d"
    calendar: Literal["UTC_24_7"] = "UTC_24_7"
    data_start: Literal["2017-08-17"] = "2017-08-17"
    learning_end_exclusive: Literal["2026-01-01"] = "2026-01-01"
    initial_cash: Literal[10000.0] = 10000.0
    slippage_bps: tuple[Literal[0.0], Literal[5.0], Literal[10.0]] = (0.0, 5.0, 10.0)
    taker_fee_bps: Literal[10.0] = 10.0
    commission: Literal[0.0] = 0.0
    max_target_weight: Literal[0.5] = 0.5
    halt_drawdown: Literal[0.2] = 0.2
    summary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class HoldoutResult(BaseModel):
    """Verified evidence recorded by the one-shot holdout pipeline."""

    model_config = ConfigDict(extra="forbid")

    candidate: Literal["donchian_breakout"]
    period: Literal["2026-01-01/2026-08-31"]
    run_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    dataset_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    total_return: float = Field(allow_inf_nan=False)
    max_drawdown: float = Field(allow_inf_nan=False)
    passed: bool

    @model_validator(mode="after")
    def pass_flag_matches_metrics(self):
        expected = self.total_return > 0 and self.max_drawdown >= -0.20
        if self.passed != expected:
            raise ValueError("holdout pass flag differs from registered thresholds")
        return self


class ExperimentSpec(BaseModel):
    """User-visible research question and all assumptions needed to run it."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=1000)
    market: Literal["us_equity", "crypto_spot"] = "us_equity"
    venue: Literal["yahoo", "binance"] = "yahoo"
    symbol: str
    interval: Literal["1d"] = "1d"
    calendar: Literal["XNYS", "UTC_24_7"] = "XNYS"
    dataset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    parent_run_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{20}$")
    prior_observed_periods: tuple[str, ...] = ()
    data: DataSpec
    strategy: StrategySpec
    initial_cash: float = Field(gt=0, allow_inf_nan=False)
    slippage_bps: tuple[float, ...] = (0, 5, 10)
    taker_fee_bps: float = Field(default=0, ge=0, lt=100, allow_inf_nan=False)
    commission: Literal[0.0] = 0.0
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)
    periods: dict[str, tuple[date, date]]

    @field_validator("title", "hypothesis")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("strategy", mode="before")
    @classmethod
    def parse_strategy(cls, value):
        if isinstance(value, (SmaStrategySpec, RsiBollingerStrategySpec, DonchianStrategySpec)):
            return value
        family = value.get("family") if isinstance(value, dict) else None
        models = {
            "sma_crossover": SmaStrategySpec,
            "rsi_bollinger": RsiBollingerStrategySpec,
            "donchian_breakout": DonchianStrategySpec,
        }
        model = models.get(family)
        if model is None:
            raise ValueError("Unsupported strategy family")
        try:
            return model.model_validate(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("symbol")
    @classmethod
    def supported_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if symbol not in {"SPY", "QQQ", "BTCUSDT"}:
            raise ValueError("Supported symbols are SPY, QQQ, and BTCUSDT")
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
        allowed = {
            ("us_equity", "yahoo", "XNYS"): {"SPY", "QQQ"},
            ("crypto_spot", "binance", "UTC_24_7"): {"BTCUSDT"},
        }
        if self.symbol not in allowed.get((self.market, self.venue, self.calendar), set()):
            raise ValueError("Unsupported market, venue, calendar, or symbol combination")
        if self.market == "crypto_spot":
            if self.taker_fee_bps != 10:
                raise ValueError("BTC MVP freezes taker_fee_bps at 10")
            if self.risk_policy.max_target_weight != 0.5:
                raise ValueError("BTC MVP freezes max_target_weight at 0.5")
        elif self.risk_policy.position_sizing.family != "fixed":
            raise ValueError("entry volatility sizing is only registered for BTC spot")
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
        if (len(set(self.prior_observed_periods)) != len(self.prior_observed_periods)
                or not set(self.prior_observed_periods).issubset(self.periods)):
            raise ValueError("prior_observed_periods must contain distinct period names")
        return self

    @property
    def engine_symbol(self) -> str:
        return self.symbol if self.market == "crypto_spot" else f"{self.symbol}.US"

    @property
    def bars_per_year(self) -> int:
        return 365 if self.calendar == "UTC_24_7" else 252

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_equity: float
    total_return: float
    cagr_252: float
    cagr_annualized: float | None = None
    annualization_days: int = Field(default=252, ge=1)
    max_drawdown: float
    round_trips: int
    fills: int
    time_in_market: float
    mean_holding_sessions: float
    sessions: int
    start: date
    end: date


class DatasetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbol: Literal["SPY", "QQQ", "BTCUSDT"]
    market: Literal["us_equity", "crypto_spot"] = "us_equity"
    venue: Literal["yahoo", "binance"] = "yahoo"
    interval: Literal["1d"] = "1d"
    calendar: Literal["XNYS", "UTC_24_7"] = "XNYS"
    base_asset: str | None = None
    quote_asset: str | None = None
    price_semantics: str = "synthetic total-return prices"
    exchange_rules: dict = Field(default_factory=dict)
    source: str
    start: date
    end: date
    retrieved_at_utc: str
    rows: int = Field(gt=0)
    adjustment: str
    raw_path: str
    adjusted_path: str
    manifest_path: str
    raw_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adjusted_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["ready"] = "ready"
