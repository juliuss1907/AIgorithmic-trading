from datetime import datetime, timezone

import pytest

from intraday.market_discovery import MarketScanner, book_metrics


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def test_liquidity_is_quote_not_base_and_impact_uses_actual_depth():
    values = book_metrics([[99, 20]], [[101, 5], [102, 10]], notional=1000)
    assert values["spread_bps"] == pytest.approx(200)
    assert values["buy_slippage_bps"] > 100
    assert values["sell_slippage_bps"] == pytest.approx(100)
    assert book_metrics([[99, 1]], [[101, 1]], notional=1000)["buy_slippage_bps"] is None
    with pytest.raises(ValueError):
        book_metrics([[101, 1]], [[99, 1]], notional=1000)
    with pytest.raises(ValueError):
        book_metrics([[99, -1]], [[101, 1]], notional=1000)


@pytest.mark.parametrize("market", ["spot", "perp"])
def test_binance_discovery_verifies_metadata_in_the_selected_demo_environment(market):
    seen = []
    def fetch(venue, scope, path, params=None, payload=None):
        seen.append((venue, scope, path))
        if path.endswith("exchangeInfo"):
            if venue == "bnb" and market == "spot":
                assert params == {"symbol":"DOGEUSDT"}
            return {"symbols": [{"symbol": "DOGEUSDT", "baseAsset": "DOGE", "quoteAsset": "USDT",
                                 "status": "TRADING", "contractType": "PERPETUAL",
                                 "orderTypes": ["MARKET", "STOP_LOSS", "STOP_MARKET"]}]}
        if path.endswith("24hr"):
            return {"symbol": "DOGEUSDT", "quoteVolume": "12500"}
        return {"bids": [[".09999", "20000"]], "asks": [[".10001", "20000"]]}
    result = MarketScanner(fetch=fetch).discover("DOGE", market=market, now=NOW)
    row = next(r for r in result["venues"] if r["venue"] == "bnb")
    assert row["selectable"] is True
    assert row["environment"] == "demo"
    assert row["volume_24h_quote"] == 12500
    assert row["instrument"] == "DOGEUSDT"
    assert all(scope == market for _, scope, _ in seen)
    assert any(r["venue"] == "variational" and r["status"] == "testnet_unverified" for r in result["venues"])


def test_one_venue_failure_does_not_hide_others_or_fallback_to_mainnet():
    def fetch(venue, market, path, params=None, payload=None):
        raise TimeoutError("untrusted response must not be echoed")
    result = MarketScanner(fetch=fetch).discover("DOGE", market="spot", now=NOW)
    assert len(result["venues"]) == 5
    assert not any(r["selectable"] for r in result["venues"])
    assert "untrusted response" not in str(result)
    assert {r["environment"] for r in result["venues"]} <= {"demo", "testnet"}
