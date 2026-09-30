import hashlib
import hmac
import json
from datetime import datetime, timezone
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
                     "commission": "0.2", "commissionAsset": "USDT", "time": int(NOW.timestamp()*1000)}]
        if url.path.endswith("algoOrder"):
            return {"algoId": 8, "clientAlgoId": "stop-1", "algoStatus": "NEW"}
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
