"""Versioned asset registry and safe per-scope lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import re

from intraday.contracts import DecisionScope


class AssetCapability(str, Enum):
    FULL = "full"
    SHADOW_ONLY = "shadow_only"
    SOAK_ONLY = "soak_only"


class AssetStage(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    SOAK = "soak"
    PAPER = "paper"


@dataclass(frozen=True)
class AssetSpec:
    symbol: str
    base_asset: str
    quote_asset: str
    enabled_scopes: frozenset[DecisionScope]
    capability: AssetCapability
    binance_spot_symbol: str | None
    binance_perp_symbol: str | None
    hyperliquid_coin: str | None
    aster_symbol: str | None
    variational_ticker: str | None
    lighter_market_id: int | None

    def __post_init__(self) -> None:
        if self.symbol != f"{self.base_asset}{self.quote_asset}":
            raise ValueError("asset symbol must match base and quote assets")
        if not self.enabled_scopes:
            raise ValueError("asset must enable at least one decision scope")
        if self.lighter_market_id is not None and self.lighter_market_id < 0:
            raise ValueError("Lighter market id must not be negative")


def _spec(
    coin: str,
    lighter_market_id: int,
    capability: AssetCapability,
) -> AssetSpec:
    symbol = f"{coin}USDT"
    return AssetSpec(
        symbol=symbol,
        base_asset=coin,
        quote_asset="USDT",
        enabled_scopes=frozenset(DecisionScope),
        capability=capability,
        binance_spot_symbol=symbol,
        binance_perp_symbol=symbol,
        hyperliquid_coin=coin,
        aster_symbol=symbol,
        variational_ticker=coin,
        lighter_market_id=lighter_market_id,
    )


# Identifiers were verified against the official public market metadata endpoints:
# Binance Spot/USD-M exchangeInfo, Hyperliquid metaAndAssetCtxs, Aster exchangeInfo,
# Variational metadata/stats, and Lighter orderBooks on 2026-09-28.
ASSET_REGISTRY: dict[str, AssetSpec] = {
    spec.symbol: spec
    for spec in (
        _spec("BTC", 1, AssetCapability.FULL),
        _spec("ETH", 0, AssetCapability.FULL),
        _spec("HYPE", 24, AssetCapability.SOAK_ONLY),
        _spec("NEAR", 10, AssetCapability.SOAK_ONLY),
        _spec("ZEC", 90, AssetCapability.SOAK_ONLY),
        _spec("SOL", 2, AssetCapability.SOAK_ONLY),
    )
}


def asset_spec(symbol: str) -> AssetSpec:
    normalized = symbol.strip().upper()
    try:
        return ASSET_REGISTRY[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported asset symbol: {normalized}") from error


def normalize_symbol(value: str) -> str:
    """Syntax only; registration/market membership is checked at service boundaries."""
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,20}USDT", normalized):
        raise ValueError("expected an alphanumeric USDT asset symbol")
    return normalized


def ticker_symbol(value: str) -> str:
    value = value.strip().upper()
    return normalize_symbol(value if value.endswith("USDT") else value + "USDT")


def spec_payload(spec: AssetSpec) -> dict:
    return {**spec.__dict__, "enabled_scopes": sorted(s.value for s in spec.enabled_scopes),
            "capability": spec.capability.value}


def spec_from_payload(payload: dict) -> AssetSpec:
    return AssetSpec(**{**payload, "enabled_scopes": frozenset(DecisionScope(s) for s in payload["enabled_scopes"]),
                        "capability": AssetCapability(payload["capability"])})


@dataclass(frozen=True)
class AssetLifecycle:
    symbol: str
    scope: DecisionScope
    stage: AssetStage = AssetStage.SHADOW
    spec: AssetSpec | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        spec = self.spec or asset_spec(self.symbol)
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "symbol", spec.symbol)
        if self.scope not in spec.enabled_scopes:
            raise ValueError("scope is not enabled for this asset")
        if (
            spec.capability is AssetCapability.SHADOW_ONLY
            and self.stage not in {AssetStage.DISABLED, AssetStage.SHADOW}
        ):
            raise ValueError(f"{spec.symbol} is shadow-only")
        if spec.capability is AssetCapability.SOAK_ONLY and self.stage is AssetStage.PAPER:
            raise ValueError(f"{spec.symbol} is decision-soak-only")

    @classmethod
    def initial(cls, spec: AssetSpec, scope: DecisionScope) -> "AssetLifecycle":
        return cls(symbol=spec.symbol, scope=scope, stage=AssetStage.SHADOW, spec=spec)

    @property
    def can_start_soak(self) -> bool:
        return (
            self.stage is AssetStage.SHADOW
            and self.spec.capability in {
                AssetCapability.FULL, AssetCapability.SOAK_ONLY
            }
        )

    def start_soak(self) -> "AssetLifecycle":
        if self.spec.capability is AssetCapability.SHADOW_ONLY:
            raise ValueError(f"{self.symbol} is shadow-only")
        if self.stage is AssetStage.SOAK:
            return self
        if self.stage is not AssetStage.SHADOW:
            raise ValueError("only a shadow asset can start soak")
        return replace(self, stage=AssetStage.SOAK)
