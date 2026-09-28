import pytest

from intraday.assets import (
    ASSET_REGISTRY,
    AssetCapability,
    AssetLifecycle,
    AssetStage,
    asset_spec,
)
from intraday.contracts import DecisionScope


EXPECTED_MAPPINGS = {
    "BTCUSDT": ("BTC", 1, AssetCapability.FULL),
    "ETHUSDT": ("ETH", 0, AssetCapability.FULL),
    "HYPEUSDT": ("HYPE", 24, AssetCapability.SHADOW_ONLY),
    "NEARUSDT": ("NEAR", 10, AssetCapability.SHADOW_ONLY),
    "ZECUSDT": ("ZEC", 90, AssetCapability.SHADOW_ONLY),
    "SOLUSDT": ("SOL", 2, AssetCapability.SHADOW_ONLY),
}


def test_asset_registry_has_verified_spot_and_perp_mappings():
    assert set(ASSET_REGISTRY) == set(EXPECTED_MAPPINGS)
    for symbol, (coin, lighter_market_id, capability) in EXPECTED_MAPPINGS.items():
        spec = asset_spec(symbol)
        assert spec.binance_spot_symbol == symbol
        assert spec.binance_perp_symbol == symbol
        assert spec.hyperliquid_coin == coin
        assert spec.aster_symbol == symbol
        assert spec.variational_ticker == coin
        assert spec.lighter_market_id == lighter_market_id
        assert spec.enabled_scopes == frozenset(DecisionScope)
        assert spec.capability is capability


def test_asset_registry_normalizes_known_symbols_and_rejects_unknown_assets():
    assert asset_spec(" ethusdt ").symbol == "ETHUSDT"
    with pytest.raises(ValueError, match="unsupported asset symbol"):
        asset_spec("DOGEUSDT")


def test_new_assets_start_shadow_and_only_full_assets_can_advance():
    eth = AssetLifecycle.initial(
        asset_spec("ETHUSDT"), DecisionScope.PERP_INTRADAY
    )
    hype = AssetLifecycle.initial(
        asset_spec("HYPEUSDT"), DecisionScope.PERP_INTRADAY
    )

    assert eth.stage is AssetStage.SHADOW
    assert eth.can_start_soak is True
    assert hype.stage is AssetStage.SHADOW
    assert hype.can_start_soak is False

    with pytest.raises(ValueError, match="shadow-only"):
        hype.start_soak()


def test_btc_lifecycle_can_preserve_an_existing_stage():
    btc = AssetLifecycle(
        symbol="BTCUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        stage=AssetStage.PAPER,
    )
    assert btc.can_start_soak is False
    assert btc.stage is AssetStage.PAPER
