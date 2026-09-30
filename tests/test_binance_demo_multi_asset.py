from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from intraday.execution.binance_demo import BinanceDemoAdapter, DemoCredentials, DemoTransport
from intraday.execution.contracts import ExecutionUnavailable, OrderIntent
from intraday.execution.binance_spot_demo import BinanceSpotDemoAdapter


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("symbol", ["ETHUSDT", "DOGEUSDT", "HYPEUSDT"])
def test_arbitrary_supported_perp_symbol_routed_and_fill_namespace(symbol):
    requests = []
    def send(request):
        url = urlsplit(request.full_url)
        params = parse_qs(url.query)
        requests.append((url, params))
        assert params["symbol"] == [symbol]
        if url.path.endswith("userTrades"):
            return [{"symbol": symbol, "side": "BUY", "id": 1, "orderId": 8, "qty": "2", "price": "10",
                     "commission": ".02", "commissionAsset": "USDT", "realizedPnl": "0", "time": int(NOW.timestamp()*1000)}]
        return {"symbol": symbol, "side": "BUY", "type": "MARKET", "origQty": "2", "orderId": 8,
                "clientOrderId": "multi-1", "status": "FILLED", "executedQty": "2", "avgPrice": "10"}
    venue = BinanceDemoAdapter(DemoTransport(DemoCredentials(api_key="fake", api_secret="fake"), send=send,
                                            clock=lambda: NOW, writes_enabled=True), symbol=symbol, market_scoped=True)
    intent = OrderIntent(intent_id="multi-1", account=venue.account_ref, symbol=symbol, market="perp",
                         side="BUY", quantity=2, created_at=NOW)
    update = venue.submit(intent)
    assert update.fills[0].fill_id == symbol + ":1"
    assert venue.account_ref.market == "perp"
    assert requests[0][0].netloc == "demo-fapi.binance.com"
    with pytest.raises(ValueError):
        venue.submit(intent.model_copy(update={"symbol": "BTCUSDT"}))


def test_spot_transport_is_distinct_and_cannot_change_account_settings():
    creds = DemoCredentials(api_key="fake", api_secret="fake")
    calls = []
    transport = DemoTransport(creds, market="spot", send=lambda request: calls.append(request) or {})
    transport.request("GET", "/api/v3/account", signed=True)
    assert urlsplit(calls[0].full_url).netloc == "demo-api.binance.com"
    with pytest.raises(ValueError):
        transport.request("GET", "/fapi/v3/account", signed=True)
    with pytest.raises(ExecutionUnavailable):
        transport.request("POST", "/api/v3/order", signed=True)
    with pytest.raises(ValueError):
        transport.request("POST", "/api/v3/account", signed=True)


def test_spot_balances_are_typed_not_synthetic_perp_positions():
    def send(request):
        if urlsplit(request.full_url).path.endswith("openOrders"):
            return []
        return {"canTrade": True, "accountType": "SPOT", "balances": [
            {"asset": "BTC", "free": "100", "locked": "0"}, {"asset": "USDT", "free": "5000", "locked": "0"}]}
    venue = BinanceSpotDemoAdapter(DemoTransport(DemoCredentials(api_key="fake", api_secret="fake"),
                                                market="spot", send=send, clock=lambda: NOW), symbol="DOGEUSDT")
    snapshot = venue.account_snapshot(now=NOW)
    assert snapshot.balance("BTC").free == 100
    assert snapshot.balance("DOGE").free == 0
    assert not hasattr(snapshot, "positions") and not hasattr(snapshot, "leverage")
    assert snapshot.account.market == "spot"


def test_spot_confirmed_partial_fills_and_native_sell_stop():
    calls = []
    def send(request):
        url = urlsplit(request.full_url); params = parse_qs(url.query)
        calls.append((request.method, url.path, params))
        if url.path.endswith("myTrades"):
            return [{"id": 3, "orderId": 8, "symbol": "DOGEUSDT", "isBuyer": True,
                     "qty": "20", "price": ".2", "commission": ".02", "commissionAsset": "DOGE",
                     "time": int(NOW.timestamp()*1000)}]
        stop = params.get("type") == ["STOP_LOSS"]
        return {"orderId": 8, "clientOrderId": "stop" if stop else "entry", "symbol": "DOGEUSDT",
                "side": "SELL" if stop else "BUY", "type": "STOP_LOSS" if stop else "MARKET",
                "origQty": "100", "stopPrice": ".18" if stop else "0", "status": "NEW" if stop else "PARTIALLY_FILLED",
                "executedQty": "0" if stop else "20"}
    venue = BinanceSpotDemoAdapter(DemoTransport(DemoCredentials(api_key="fake", api_secret="fake"),
                                                market="spot", send=send, clock=lambda: NOW, writes_enabled=True), symbol="DOGEUSDT")
    entry = OrderIntent(intent_id="entry", account=venue.account_ref, market="spot", symbol="DOGEUSDT",
                        side="BUY", quantity=100, created_at=NOW)
    update = venue.submit(entry)
    assert update.status == "PARTIALLY_FILLED" and update.fills[0].commission_asset == "DOGE"
    stop = entry.model_copy(update={"intent_id": "stop", "side": "SELL", "order_type": "STOP_LOSS", "stop_price": Decimal(".18")})
    assert venue.submit(stop).status == "NEW"
    assert calls[-1][2]["stopPrice"] == ["0.18"]
    assert "reduceOnly" not in calls[-1][2]
    assert all(urlsplit("https://demo-api.binance.com" + c[1]).netloc == "demo-api.binance.com" for c in calls)


def test_spot_filters_ignore_disabled_market_steps_and_apply_notional():
    metadata = {"symbols": [{"symbol": "DOGEUSDT", "baseAsset": "DOGE", "quoteAsset": "USDT", "status": "TRADING",
                             "isSpotTradingAllowed": True, "orderTypes": ["MARKET", "STOP_LOSS"], "filters": [
        {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "1000000", "stepSize": "1"},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0", "maxQty": "0", "stepSize": "0"},
        {"filterType": "PRICE_FILTER", "tickSize": ".0001"},
        {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "5000", "applyMinToMarket": True, "applyMaxToMarket": True}]}]}
    calls = []
    venue = BinanceSpotDemoAdapter(DemoTransport(DemoCredentials(api_key="fake", api_secret="fake"), market="spot",
                                                send=lambda r: calls.append(r) or metadata), symbol="DOGEUSDT")
    rules = venue.instrument("DOGEUSDT")
    assert rules.quantity_step == 1 and rules.max_notional == 5000
    assert parse_qs(urlsplit(calls[0].full_url).query)["symbol"] == ["DOGEUSDT"]
    with pytest.raises(ValueError, match="minimum"):
        rules.validate_quantity(Decimal(10), Decimal(".2"))
    with pytest.raises(ValueError, match="maximum"):
        rules.validate_quantity(Decimal(100000), Decimal(".2"))
