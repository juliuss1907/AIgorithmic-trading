import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from intraday.execution.binance_demo import BinanceDemoAdapter, DemoCredentials, DemoTransport
from intraday.execution.contracts import ExecutionUnavailable, OrderIntent


NOW = datetime(2026, 9, 30, 3, tzinfo=timezone.utc)


def credentials():
    return DemoCredentials(api_key="test-key", api_secret="test-secret")


def intent(adapter, **changes):
    values = dict(intent_id="demo-entry-1", account=adapter.account_ref, symbol="BTCUSDT",
                  market="perp", side="BUY", quantity=Decimal("0.01"), created_at=NOW)
    values.update(changes)
    return OrderIntent(**values)


def test_credentials_are_redacted_and_require_private_regular_file(tmp_path):
    secret = tmp_path / "demo.json"
    secret.write_text(json.dumps({"api_key": "test-key", "api_secret": "test-secret"}))
    secret.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        DemoCredentials.load(secret)
    secret.chmod(0o600)
    creds = DemoCredentials.load(secret)
    assert "test-secret" not in repr(creds)
    assert "test-key" not in repr(creds)
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(ValueError):
        DemoCredentials.load(link)


def test_signed_transport_fixed_demo_host_and_safe_default():
    seen = []
    transport = DemoTransport(credentials(), send=lambda request: seen.append(request) or {}, clock=lambda: NOW)
    transport.request("GET", "/fapi/v3/account", signed=True)
    request = seen[0]
    url = urlsplit(request.full_url)
    assert url.netloc == "demo-fapi.binance.com"
    raw, signature = url.query.rsplit("&signature=", 1)
    assert signature == hmac.new(b"test-secret", raw.encode(), hashlib.sha256).hexdigest()
    assert parse_qs(raw)["timestamp"] == [str(int(NOW.timestamp() * 1000))]
    assert request.get_header("X-mbx-apikey") == "test-key"
    with pytest.raises(ExecutionUnavailable, match="disabled"):
        transport.request("POST", "/fapi/v1/order", signed=True)
    with pytest.raises(ValueError):
        transport.request("GET", "https://fapi.binance.com/fapi/v1/order", signed=True)
    assert len(seen) == 1


def test_market_ack_partial_fills_and_native_stop_route():
    calls = []
    def send(request):
        url = urlsplit(request.full_url)
        calls.append((request.method, url.path, parse_qs(url.query)))
        if url.path.endswith("userTrades"):
            return [{"id": 7, "orderId": 1, "qty": "0.004", "price": "100000",
                     "commission": "0.2", "commissionAsset": "USDT", "realizedPnl": "0", "time": int(NOW.timestamp()*1000)}]
        if url.path.endswith("algoOrder"):
            return {"algoId": 8, "clientAlgoId": "stop-1", "algoStatus": "NEW", "symbol": "BTCUSDT",
                    "side": "SELL", "positionSide": "BOTH", "orderType": "STOP_MARKET", "workingType": "MARK_PRICE",
                    "reduceOnly": True, "quantity": "0.01", "triggerPrice": "99000"}
        return {"orderId": 1, "clientOrderId": "demo-entry-1", "symbol": "BTCUSDT",
                "status": "PARTIALLY_FILLED", "executedQty": "0.004", "avgPrice": "100000"}
    adapter = BinanceDemoAdapter(DemoTransport(credentials(), send=send, clock=lambda: NOW, writes_enabled=True))
    update = adapter.submit(intent(adapter))
    assert update.status == "PARTIALLY_FILLED"
    assert update.executed_quantity == Decimal("0.004")
    assert update.fills[0].commission == Decimal("0.2")
    assert calls[0][1] == "/fapi/v1/order"
    assert calls[0][2]["newClientOrderId"] == ["demo-entry-1"]
    stop = adapter.submit(intent(adapter, intent_id="stop-1", side="SELL", reduce_only=True,
                                 order_type="STOP_MARKET", stop_price=Decimal("99000")))
    assert stop.status == "NEW"
    assert calls[-1][1] == "/fapi/v1/algoOrder"
    assert calls[-1][2]["workingType"] == ["MARK_PRICE"]
    assert calls[-1][2]["reduceOnly"] == ["true"]


def test_transport_sanitizes_timeout_without_leaking_credentials():
    def fail(request):
        raise TimeoutError("test-secret test-key " + request.full_url)
    transport = DemoTransport(credentials(), send=fail, writes_enabled=True)
    with pytest.raises(ExecutionUnavailable) as error:
        transport.request("POST", "/fapi/v1/order", signed=True)
    assert "test-secret" not in str(error.value)
    assert "signature" not in str(error.value)


def test_filters_use_decimal_market_and_lot_intersection():
    response = {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL",
                            "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
        {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "100", "stepSize": "0.001"},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0.002", "maxQty": "10", "stepSize": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "100"},
    ]}]}
    adapter = BinanceDemoAdapter(DemoTransport(credentials(), send=lambda _: response))
    rules = adapter.instrument("BTCUSDT")
    assert rules.round_quantity(Decimal("0.00399")) == Decimal("0.003")
    assert rules.min_quantity == Decimal("0.002")
    assert rules.max_quantity == Decimal("10")
    with pytest.raises(ValueError):
        rules.validate_quantity(Decimal("0.001"), Decimal("100000"))


def test_real_transport_routes_quote_and_account_without_mutations():
    calls = []
    clock = [NOW]
    def send(request):
        path = urlsplit(request.full_url).path
        calls.append((request.method, path))
        clock[0] += timedelta(seconds=3)
        timestamp = int(clock[0].timestamp()*1000)
        return {
            "/fapi/v1/ticker/bookTicker": {"bidPrice": "99990", "askPrice": "100010", "time": timestamp},
            "/fapi/v1/premiumIndex": {"markPrice": "100000", "time": timestamp},
            "/fapi/v3/account": {"totalWalletBalance": "10000", "totalMarginBalance": "10000", "availableBalance": "10000", "canTrade": True},
            "/fapi/v3/positionRisk": [],
            "/fapi/v1/symbolConfig": [{"symbol": "BTCUSDT", "marginType": "ISOLATED", "leverage": 3, "isAutoAddMargin": False}],
            "/fapi/v1/positionSide/dual": {"dualSidePosition": False},
            "/fapi/v1/multiAssetsMargin": {"multiAssetsMargin": False},
            "/fapi/v1/openOrders": [],
            "/fapi/v1/openAlgoOrders": [],
        }[path]
    adapter = BinanceDemoAdapter(DemoTransport(credentials(), send=send, clock=lambda: clock[0]))
    assert adapter.quote("BTCUSDT", now=NOW).mark == 100000
    snapshot = adapter.account_snapshot(now=clock[0])
    assert snapshot.one_way and snapshot.single_asset and snapshot.margin_mode == "ISOLATED"
    assert snapshot.wallet_balance == 10000
    assert all(method == "GET" for method, _ in calls)


def test_unknown_lookup_not_found_does_not_submit():
    calls = []
    def send(request):
        calls.append(request.method)
        return {"code": -2013, "msg": "Order does not exist"}
    adapter = BinanceDemoAdapter(DemoTransport(credentials(), send=send))
    assert adapter.query(intent(adapter)) is None
    assert calls == ["GET"]


def test_triggered_stop_queries_child_order_and_records_actual_fill():
    calls = []
    def send(request):
        path = urlsplit(request.full_url).path
        calls.append(path)
        if path.endswith("algoOrder"):
            return {"algoId": 8, "clientAlgoId": "stop-1", "algoStatus": "FINISHED", "actualOrderId": 19,
                    "symbol": "BTCUSDT", "side": "SELL", "positionSide": "BOTH", "orderType": "STOP_MARKET",
                    "workingType": "MARK_PRICE", "reduceOnly": True, "quantity": ".01", "triggerPrice": "99000"}
        if path.endswith("userTrades"):
            return [{"id": 21, "orderId": 19, "qty": ".01", "price": "98950", "realizedPnl": "-10.5",
                     "commission": ".49475", "commissionAsset": "USDT", "time": int(NOW.timestamp()*1000)}]
        return {"orderId": 19, "status": "FILLED", "executedQty": ".01", "avgPrice": "98950", "symbol": "BTCUSDT"}
    adapter = BinanceDemoAdapter(DemoTransport(credentials(), send=send, clock=lambda: NOW))
    stop = intent(adapter, intent_id="stop-1", order_type="STOP_MARKET", side="SELL", reduce_only=True, stop_price=Decimal(99000))
    update = adapter.query(stop)
    assert update.status == "FILLED"
    assert update.fills[0].realized_pnl == Decimal("-10.5")
    assert update.exchange_order_id == "19"
    assert calls == ["/fapi/v1/algoOrder", "/fapi/v1/order", "/fapi/v1/userTrades"]


def test_explicit_margin_rejection_is_terminal_not_uncertain():
    adapter = BinanceDemoAdapter(DemoTransport(credentials(), send=lambda _: {"code": -2019, "msg": "Margin insufficient"}, writes_enabled=True))
    assert adapter.submit(intent(adapter)).status == "REJECTED"
