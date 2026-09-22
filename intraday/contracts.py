"""Strict, versioned contracts for the intraday decision and audit pipeline."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


class Direction(str, Enum):
    STRONG_BUY = "Strong Buy"
    BUY = "Buy"
    HOLD = "Hold"
    TAKE_PROFIT = "Take Profit"
    SELL = "Sell"
    STRONG_SELL = "Strong Sell"


class Regime(str, Enum):
    TRENDING_UP = "Trending Up"
    TRENDING_DOWN = "Trending Down"
    SIDEWAYS = "Sideways"
    VOLATILE = "Volatile"


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class SourceTier(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class ProviderRole(str, Enum):
    JEV = "jev"
    LLM = "llm"


class ProviderKind(str, Enum):
    OPENROUTER_DECISIONS = "openrouter-decisions"
    OPENAI_COMPATIBLE = "openai-compatible"


class NewsSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)
    schema_version: Literal["1"] = "1"


class FeatureSnapshot(StrictContract):
    snapshot_id: str = Field(min_length=16, max_length=64)
    symbol: Literal["BTCUSDT"] = "BTCUSDT"
    event_time: datetime
    built_at: datetime
    bid: float = Field(gt=0, allow_inf_nan=False)
    ask: float = Field(gt=0, allow_inf_nan=False)
    features: dict[str, float | None]
    freshness: dict[str, bool]
    quality_flags: tuple[str, ...] = ()
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")

    _event_time_is_aware = field_validator("event_time")(_aware)
    _built_at_is_aware = field_validator("built_at")(_aware)

    @model_validator(mode="after")
    def valid_market_state(self):
        if self.bid > self.ask:
            raise ValueError("bid must not exceed ask")
        if not self.features:
            raise ValueError("features cannot be empty")
        if not self.freshness:
            raise ValueError("freshness cannot be empty")
        for name, value in self.features.items():
            if not name or (value is not None and not isinstance(value, (int, float))):
                raise ValueError("features must contain named numeric values")
        expected = self._checksum_for(
            symbol=self.symbol,
            event_time=self.event_time,
            built_at=self.built_at,
            bid=self.bid,
            ask=self.ask,
            features=self.features,
            freshness=self.freshness,
            quality_flags=self.quality_flags,
        )
        if self.checksum != expected or self.snapshot_id != expected[:24]:
            raise ValueError("snapshot checksum does not match its payload")
        return self

    @staticmethod
    def _checksum_for(
        *, symbol, event_time, built_at, bid, ask, features, freshness, quality_flags
    ) -> str:
        payload = {
            "symbol": symbol,
            "event_time": event_time.isoformat(),
            "built_at": built_at.isoformat(),
            "bid": float(bid),
            "ask": float(ask),
            "features": {
                name: None if value is None else float(value)
                for name, value in features.items()
            },
            "freshness": {name: bool(value) for name, value in freshness.items()},
            "quality_flags": tuple(quality_flags),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        event_time: datetime,
        built_at: datetime,
        bid: float,
        ask: float,
        features: dict[str, float | None],
        freshness: dict[str, bool],
        quality_flags: tuple[str, ...] = (),
    ) -> "FeatureSnapshot":
        checksum = cls._checksum_for(
            symbol=symbol,
            event_time=event_time,
            built_at=built_at,
            bid=bid,
            ask=ask,
            features=features,
            freshness=freshness,
            quality_flags=quality_flags,
        )
        return cls(
            snapshot_id=checksum[:24],
            checksum=checksum,
            symbol=symbol,
            event_time=event_time,
            built_at=built_at,
            bid=bid,
            ask=ask,
            features=features,
            freshness=freshness,
            quality_flags=quality_flags,
        )


class JevDecision(StrictContract):
    decision_id: str = Field(min_length=1, max_length=128)
    tick_id: str = Field(min_length=1, max_length=160)
    snapshot_id: str = Field(min_length=1, max_length=64)
    direction: Direction
    direction_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    regime: Regime
    toxic_flow: float = Field(ge=0, le=1, allow_inf_nan=False)
    entry_quality: float = Field(ge=1, le=5, allow_inf_nan=False)
    risk_level: RiskLevel
    model_ref: str = Field(min_length=1, max_length=160)
    created_at: datetime
    latency_ms: int | None = Field(default=None, ge=0)
    provider_request_id: str | None = Field(default=None, max_length=200)

    _created_at_is_aware = field_validator("created_at")(_aware)


class RiskState(StrictContract):
    equity: float = Field(gt=0, allow_inf_nan=False)
    high_water_mark: float = Field(gt=0, allow_inf_nan=False)
    daily_return: float = Field(allow_inf_nan=False)
    drawdown: float = Field(le=0, allow_inf_nan=False)
    halted: bool = False
    halt_reason: str | None = Field(default=None, max_length=120)
    news_pause_until: datetime | None = None
    last_entry_at: datetime | None = None

    @field_validator("news_pause_until", "last_entry_at")
    @classmethod
    def optional_time_is_aware(cls, value):
        return None if value is None else _aware(value)


class PositionSnapshot(StrictContract):
    tranches: int = Field(ge=-2, le=2)
    quantity: float = Field(allow_inf_nan=False)
    entry_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    mark_price: float = Field(gt=0, allow_inf_nan=False)
    notional: float = Field(ge=0, allow_inf_nan=False)
    leverage: Literal[3] = 3
    isolated_margin: float = Field(ge=0, allow_inf_nan=False)
    maintenance_margin: float = Field(ge=0, allow_inf_nan=False)
    liquidation_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    liquidation_buffer: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    funding: float = Field(allow_inf_nan=False)
    unrealized_pnl: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def consistent_side(self):
        if self.tranches == 0:
            if self.quantity != 0 or self.notional != 0 or self.entry_price is not None:
                raise ValueError("flat position must have zero quantity/notional and no entry")
        elif self.quantity == 0 or (self.tranches > 0) != (self.quantity > 0):
            raise ValueError("quantity side must match tranches")
        return self

    @classmethod
    def flat(cls, mark_price: float) -> "PositionSnapshot":
        return cls(
            tranches=0,
            quantity=0,
            entry_price=None,
            mark_price=mark_price,
            notional=0,
            isolated_margin=0,
            maintenance_margin=0,
            liquidation_price=None,
            liquidation_buffer=None,
            funding=0,
            unrealized_pnl=0,
        )


class CrossVenueAssessment(StrictContract):
    status: Literal["unavailable", "neutral", "confirming", "conflicting", "stressed"]
    entry_quality_delta: float = Field(ge=-1, le=0.5, allow_inf_nan=False)
    notional_multiplier: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason_codes: tuple[str, ...] = ()
    policy_version: str = Field(min_length=1, max_length=80)
    evaluated_at: datetime

    _evaluated_at_is_aware = field_validator("evaluated_at")(_aware)


class GateDecision(StrictContract):
    gate_id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    rule_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["authorized", "reduce_only", "hold"]
    target_tranches: int = Field(ge=-2, le=2)
    authorized_notional: float = Field(ge=0, allow_inf_nan=False)
    reason_codes: tuple[str, ...]
    evaluated_at: datetime
    cross_venue_mode: Literal["off", "shadow", "active"] = "off"
    cross_venue_status: str | None = None
    cross_venue_applied: bool = False
    entry_quality_adjustment: float = Field(default=0, ge=-1, le=0.5)
    effective_entry_quality: float | None = Field(default=None, ge=1, le=5)
    notional_multiplier: float = Field(default=1, ge=0, le=1)

    _evaluated_at_is_aware = field_validator("evaluated_at")(_aware)


class PaperFill(StrictContract):
    fill_id: str = Field(min_length=1, max_length=128)
    order_id: str = Field(min_length=1, max_length=128)
    gate_id: str = Field(min_length=1, max_length=128)
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False)
    notional: float = Field(gt=0, allow_inf_nan=False)
    fee: float = Field(ge=0, allow_inf_nan=False)
    slippage: float = Field(ge=0, allow_inf_nan=False)
    reduce_only: bool
    filled_at: datetime

    _filled_at_is_aware = field_validator("filled_at")(_aware)


class NewsEvent(StrictContract):
    event_id: str = Field(min_length=1, max_length=160)
    source_id: str = Field(min_length=1, max_length=100)
    source_tier: SourceTier
    title: str = Field(min_length=5, max_length=500)
    url: str = Field(min_length=8, max_length=2048)
    published_at: datetime
    received_at: datetime
    category: str = Field(min_length=1, max_length=100)
    severity: NewsSeverity
    summary: str | None = Field(default=None, max_length=2000)

    _published_at_is_aware = field_validator("published_at")(_aware)
    _received_at_is_aware = field_validator("received_at")(_aware)


class NewsCluster(StrictContract):
    cluster_id: str = Field(min_length=16, max_length=64)
    title: str
    category: str
    severity: NewsSeverity
    source_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    verified: bool
    first_seen_at: datetime
    last_seen_at: datetime

    _first_seen_is_aware = field_validator("first_seen_at")(_aware)
    _last_seen_is_aware = field_validator("last_seen_at")(_aware)


class NewsIngestResult(StrictContract):
    accepted_events: tuple[NewsEvent, ...]
    clusters: tuple[NewsCluster, ...]
    pause_until: datetime | None

    @field_validator("pause_until")
    @classmethod
    def pause_time_is_aware(cls, value):
        return None if value is None else _aware(value)


class VenueMarketFrame(StrictContract):
    """Content-addressed, normalized market evidence from one external venue."""

    frame_id: str = Field(min_length=16, max_length=64)
    venue: Literal["hyperliquid", "lighter"]
    symbol: Literal["BTCUSDT"] = "BTCUSDT"
    event_time: datetime
    received_at: datetime
    metadata_received_at: datetime
    bid: float = Field(gt=0, allow_inf_nan=False)
    ask: float = Field(gt=0, allow_inf_nan=False)
    mark_price: float = Field(gt=0, allow_inf_nan=False)
    index_price: float = Field(gt=0, allow_inf_nan=False)
    funding_bps_hour: float = Field(allow_inf_nan=False)
    open_interest_usd: float = Field(ge=0, allow_inf_nan=False)
    spread_bps: float = Field(ge=0, allow_inf_nan=False)
    bid_depth_usd: dict[str, float]
    ask_depth_usd: dict[str, float]
    book_imbalance: dict[str, float]
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")

    _event_time_is_aware = field_validator("event_time")(_aware)
    _received_at_is_aware = field_validator("received_at")(_aware)
    _metadata_received_at_is_aware = field_validator("metadata_received_at")(_aware)

    @model_validator(mode="after")
    def valid_frame(self):
        if self.bid > self.ask:
            raise ValueError("order book is crossed")
        expected_keys = {"5", "10", "25"}
        if not all(set(values) == expected_keys for values in (
            self.bid_depth_usd, self.ask_depth_usd, self.book_imbalance
        )):
            raise ValueError("depth maps must contain 5, 10, and 25 bps buckets")
        if any(value < 0 for mapping in (self.bid_depth_usd, self.ask_depth_usd) for value in mapping.values()):
            raise ValueError("depth cannot be negative")
        if any(not -1 <= value <= 1 for value in self.book_imbalance.values()):
            raise ValueError("book imbalance must be between -1 and 1")
        expected = self._checksum_for(self.model_dump(exclude={"frame_id", "checksum"}))
        if self.checksum != expected or self.frame_id != expected[:24]:
            raise ValueError("frame checksum does not match its payload")
        return self

    @staticmethod
    def _checksum_for(payload: dict) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def create(cls, **values) -> "VenueMarketFrame":
        payload = {"schema_version": "1", **values}
        checksum = cls._checksum_for(payload)
        return cls(frame_id=checksum[:24], checksum=checksum, **values)


class ProviderProfile(StrictContract):
    """Redacted provider metadata safe to persist and return from the web API."""

    profile_id: str = Field(
        min_length=3,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    role: ProviderRole
    kind: ProviderKind
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    credential_version: str = Field(min_length=1, max_length=100)
    created_at: datetime
    updated_at: datetime
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    _profile_created_is_aware = field_validator("created_at")(_aware)
    _profile_updated_is_aware = field_validator("updated_at")(_aware)

    @model_validator(mode="after")
    def valid_provider_endpoint(self):
        parsed = urlsplit(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider URL must not contain credentials, query, or fragment")
        if self.kind == ProviderKind.OPENROUTER_DECISIONS:
            if self.role != ProviderRole.JEV:
                raise ValueError("OpenRouter Decisions profiles must use the jev role")
            if self.base_url.rstrip("/") != "https://openrouter.ai/api/alpha/decisions":
                raise ValueError("OpenRouter Decisions endpoint is fixed")
        elif parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("OpenAI-compatible provider URL must use HTTPS")
        expected = self._fingerprint_for(
            profile_id=self.profile_id,
            role=self.role,
            kind=self.kind,
            base_url=self.base_url,
            model=self.model,
            credential_version=self.credential_version,
        )
        if self.fingerprint != expected:
            raise ValueError("provider profile fingerprint does not match")
        return self

    @staticmethod
    def _fingerprint_for(**values) -> str:
        payload = {
            key: value.value if isinstance(value, Enum) else value
            for key, value in values.items()
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def create(cls, **values) -> "ProviderProfile":
        fingerprint = cls._fingerprint_for(
            **{
                key: values[key]
                for key in (
                    "profile_id", "role", "kind", "base_url", "model",
                    "credential_version",
                )
            }
        )
        return cls(fingerprint=fingerprint, **values)


class ModelCallRecord(StrictContract):
    call_id: str = Field(min_length=1, max_length=128)
    workflow: str = Field(min_length=1, max_length=80)
    role: ProviderRole
    profile_id: str = Field(min_length=3, max_length=80)
    profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: str = Field(min_length=1, max_length=200)
    status: Literal["success", "error"]
    started_at: datetime
    completed_at: datetime
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    provider_request_id: str | None = Field(default=None, max_length=200)
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    response_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    error_code: str | None = Field(default=None, max_length=80)

    _call_started_is_aware = field_validator("started_at")(_aware)
    _call_completed_is_aware = field_validator("completed_at")(_aware)

    @model_validator(mode="after")
    def valid_call_result(self):
        if self.completed_at < self.started_at:
            raise ValueError("model call completion precedes start")
        if self.status == "success" and self.error_code is not None:
            raise ValueError("successful model call cannot have an error code")
        if self.status == "error" and not self.error_code:
            raise ValueError("failed model call requires an error code")
        return self


class RuleParameters(StrictContract):
    """Only model-tunable filters; hard risk limits deliberately do not appear here."""

    confidence_threshold: float = Field(default=0.85, ge=0.85, le=0.98)
    entry_quality_min: float = Field(default=3, ge=3, le=5)
    toxic_flow_max: float = Field(default=0.30, ge=0.05, le=0.30)
    stop_distance_pct: float = Field(default=0.01, ge=0.0075, le=0.03)
    allowed_regimes: tuple[Regime, ...] = (
        Regime.TRENDING_UP,
        Regime.TRENDING_DOWN,
        Regime.SIDEWAYS,
        Regime.VOLATILE,
    )

    @field_validator("allowed_regimes")
    @classmethod
    def regimes_are_unique_and_nonempty(cls, value):
        if not value or len(value) != len(set(value)):
            raise ValueError("allowed regimes must be nonempty and unique")
        return value


class RuleCandidate(StrictContract):
    rule_id: str = Field(min_length=1, max_length=128)
    parent_rule_id: str = Field(min_length=1, max_length=128)
    thesis_id: str = Field(min_length=1, max_length=128)
    parameters: RuleParameters
    created_at: datetime
    model_ref: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=80)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    _candidate_time_is_aware = field_validator("created_at")(_aware)

    @classmethod
    def create(cls, **values) -> "RuleCandidate":
        payload = {
            key: (value.model_dump(mode="json") if isinstance(value, BaseModel) else value)
            for key, value in values.items()
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return cls(content_hash=hashlib.sha256(encoded).hexdigest(), **values)


class PromotionEvaluation(StrictContract):
    candidate_id: str
    champion_id: str
    started_at: datetime
    evaluated_at: datetime
    coverage: float = Field(ge=0, le=1)
    champion_score: float
    challenger_score: float
    status: Literal["deferred", "reject", "promote"]
    reasons: tuple[str, ...] = ()

    _promotion_started_is_aware = field_validator("started_at")(_aware)
    _promotion_evaluated_is_aware = field_validator("evaluated_at")(_aware)
