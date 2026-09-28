"""Versioned asset registry and safe per-scope lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from intraday.contracts import DecisionScope


class AssetCapability(str, Enum):
    FULL = "full"
    SHADOW_ONLY = "shadow_only"


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
    binance_spot_symbol: str
    binance_perp_symbol: str
    hyperliquid_coin: str
    aster_symbol: str
    variational_ticker: str
    lighter_market_id: int

    def __post_init__(self) -> None:
        if self.symbol != f"{self.base_asset}{self.quote_asset}":
            raise ValueError("asset symbol must match base and quote assets")
        if not self.enabled_scopes:
            raise ValueError("asset must enable at least one decision scope")
        if self.lighter_market_id < 0:
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
        _spec("HYPE", 24, AssetCapability.SHADOW_ONLY),
        _spec("NEAR", 10, AssetCapability.SHADOW_ONLY),
        _spec("ZEC", 90, AssetCapability.SHADOW_ONLY),
        _spec("SOL", 2, AssetCapability.SHADOW_ONLY),
    )
}


def asset_spec(symbol: str) -> AssetSpec:
    normalized = symbol.strip().upper()
    try:
        return ASSET_REGISTRY[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported asset symbol: {normalized}") from error


@dataclass(frozen=True)
class AssetLifecycle:
    symbol: str
    scope: DecisionScope
    stage: AssetStage = AssetStage.SHADOW

    def __post_init__(self) -> None:
        spec = asset_spec(self.symbol)
        object.__setattr__(self, "symbol", spec.symbol)
        if self.scope not in spec.enabled_scopes:
            raise ValueError("scope is not enabled for this asset")

    @classmethod
    def initial(cls, spec: AssetSpec, scope: DecisionScope) -> "AssetLifecycle":
        return cls(symbol=spec.symbol, scope=scope, stage=AssetStage.SHADOW)

    @property
    def can_start_soak(self) -> bool:
        return (
            self.stage is AssetStage.SHADOW
            and asset_spec(self.symbol).capability is AssetCapability.FULL
        )

    def start_soak(self) -> "AssetLifecycle":
        if asset_spec(self.symbol).capability is AssetCapability.SHADOW_ONLY:
            raise ValueError(f"{self.symbol} is shadow-only")
        if self.stage is not AssetStage.SHADOW:
            raise ValueError("only a shadow asset can start soak")
        return replace(self, stage=AssetStage.SOAK)
