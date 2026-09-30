"""USD-M Demo only. No production host, redirects, or implicit account changes."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import stat
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, SecretStr

from intraday.execution.contracts import (
    AccountRef, AccountSnapshot, ExecutionFill, ExecutionUnavailable,
    InstrumentRules, OpenOrder, OrderIntent, OrderUpdate, Position, Quote,
)


DEMO_HOST = "https://demo-fapi.binance.com"
SYMBOL = "BTCUSDT"
READ_ROUTES = {
    "/fapi/v1/time", "/fapi/v1/exchangeInfo", "/fapi/v1/ticker/bookTicker", "/fapi/v1/premiumIndex",
    "/fapi/v3/account", "/fapi/v1/accountConfig", "/fapi/v3/positionRisk", "/fapi/v1/symbolConfig", "/fapi/v1/positionSide/dual",
    "/fapi/v1/multiAssetsMargin", "/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders", "/fapi/v1/income",
    "/fapi/v1/order", "/fapi/v1/algoOrder", "/fapi/v1/userTrades",
}


class DemoCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_key: SecretStr
    api_secret: SecretStr

    @classmethod
    def load(cls, path: Path):
        if path.name == ".env" or path.name.startswith(".env."):
            raise ValueError("use a dedicated Demo JSON secret file, not an environment file")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            raise ValueError("Demo credentials require a regular 0600 file") from None
        with os.fdopen(descriptor) as source:
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_uid != os.geteuid() or metadata.st_size > 8192):
                raise ValueError("Demo credentials require an owned regular 0600 file")
            try:
                result = cls.model_validate_json(source.read(8193))
            except (ValueError, UnicodeError):
                raise ValueError("invalid Demo credential file; expected api_key and api_secret") from None
        if not result.api_key.get_secret_value() or not result.api_secret.get_secret_value():
            raise ValueError("Demo credentials must be nonempty")
        return result

    @property
    def account_ref(self):
        # Stable local identity, not Binance's account UID. Key rotation needs reactivation.
        fingerprint = hashlib.sha256(self.api_key.get_secret_value().encode()).hexdigest()[:24]
        return AccountRef(venue="binance", environment="demo", account_id=fingerprint)


class DemoApiError(ExecutionUnavailable):
    def __init__(self, code: int | None, status: int | None = None):
        self.code = code
        self.http_status = status
        super().__init__(f"Binance Demo API failure (code={code}, HTTP={status})")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send(request):
    # Do not use an opener that follows redirects with signed headers.
    with build_opener(_NoRedirect()).open(request, timeout=10) as response:
        return json.loads(response.read(2_000_001))


class DemoTransport:
    def __init__(self, credentials: DemoCredentials, *, send=None, clock=None, writes_enabled=False):
        self.credentials = credentials
        self._send = send or _send
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.writes_enabled = writes_enabled

    def request(self, method, path, parameters=None, *, signed=False):
        if method not in {"GET", "POST", "DELETE"} or path not in READ_ROUTES:
            raise ValueError("invalid Demo API route")
        if method != "GET":
            if not self.writes_enabled:
                raise ExecutionUnavailable("Demo order submission disabled")
            if not signed or path not in {"/fapi/v1/order", "/fapi/v1/algoOrder"}:
                raise ValueError("Demo mutation outside order interface")
        values = dict(parameters or {})
        headers = {}
        if signed:
            values.update(timestamp=int(self.clock().timestamp() * 1000), recvWindow=5000)
            headers["X-MBX-APIKEY"] = self.credentials.api_key.get_secret_value()
        encoded = urlencode(values)
        if signed:
            signature = hmac.new(self.credentials.api_secret.get_secret_value().encode(),
                                 encoded.encode(), hashlib.sha256).hexdigest()
            encoded += "&signature=" + signature
        request = Request(DEMO_HOST + path + ("?" + encoded if encoded else ""),
                          headers=headers, method=method)
        try:
            result = self._send(request)
        except HTTPError as error:
            try:
                code = int(json.loads(error.read(8192)).get("code"))
            except (ValueError, TypeError, AttributeError):
                code = None
            raise DemoApiError(code, error.code) from None
        except Exception:
            # An exception may contain a full signed URL or echoed API key. Never propagate it.
            raise ExecutionUnavailable("Binance Demo transport failure; reconcile order status") from None
        if isinstance(result, dict) and isinstance(result.get("code"), int) and result["code"] < 0:
            raise DemoApiError(result["code"])
        return result


class BinanceDemoAdapter:
    def __init__(self, transport: DemoTransport):
        self.transport = transport
        self.account_ref = transport.credentials.account_ref

    def _check(self, intent):
        if intent.account != self.account_ref or intent.market != "perp" or intent.symbol != SYMBOL:
            raise ValueError("Binance Demo adapter supports only its BTCUSDT Perp account")

    def _normalize(self, intent, response, *, algo=False):
        now = self.transport.clock()
        client_id = response.get("clientAlgoId" if algo else "clientOrderId")
        if client_id != intent.intent_id:
            raise ExecutionUnavailable("Demo order identity mismatch")
        if algo:
            expected = {"symbol": SYMBOL, "side": intent.side, "positionSide": "BOTH",
                        "orderType": "STOP_MARKET", "workingType": "MARK_PRICE", "reduceOnly": True}
            if (any(response.get(name) != value for name, value in expected.items())
                    or Decimal(response["quantity"]) != intent.quantity
                    or Decimal(response["triggerPrice"]) != intent.stop_price):
                raise ExecutionUnavailable("Demo protective order differs from persisted intent")
        if algo and response.get("actualOrderId"):
            child = self.transport.request("GET", "/fapi/v1/order",
                                           {"symbol": SYMBOL, "orderId": response["actualOrderId"]}, signed=True)
            return self._regular_update(intent, child, now)
        status = response.get("algoStatus" if algo else "status", "UNKNOWN")
        if status in {"TRIGGERED", "FINISHED"}:
            # Missing child identity is not proof of a fill or successful close.
            status = "UNKNOWN"
        if status not in {"UNKNOWN", "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
            status = "UNKNOWN"
        if algo:
            return OrderUpdate(intent_id=intent.intent_id, status=status,
                               exchange_order_id=str(response["algoId"]), received_at=now)
        return self._regular_update(intent, response, now, status=status)

    def _regular_update(self, intent, response, now, status=None):
        executed = Decimal(response.get("executedQty", "0"))
        if executed > intent.quantity or response.get("symbol", SYMBOL) != SYMBOL:
            raise ExecutionUnavailable("Demo executed order differs from persisted intent")
        fills = ()
        if executed:
            trades = self.transport.request("GET", "/fapi/v1/userTrades",
                                            {"symbol": SYMBOL, "orderId": response["orderId"], "limit": 1000}, signed=True)
            if len(trades) >= 1000:
                raise ExecutionUnavailable("Demo fill history requires operator reconciliation")
            fills = tuple(ExecutionFill(
                fill_id=f"{SYMBOL}:{row['id']}", quantity=Decimal(row["qty"]), price=Decimal(row["price"]),
                commission=Decimal(row["commission"]), commission_asset=row["commissionAsset"],
                realized_pnl=Decimal(row["realizedPnl"]),
                filled_at=datetime.fromtimestamp(int(row["time"]) / 1000, timezone.utc),
            ) for row in trades if str(row["orderId"]) == str(response["orderId"]))
        status = status or response.get("status", "UNKNOWN")
        if status not in {"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
            status = "UNKNOWN"
        average = Decimal(response.get("avgPrice", "0"))
        return OrderUpdate(intent_id=intent.intent_id, status=status,
                           exchange_order_id=str(response["orderId"]), executed_quantity=executed,
                           average_price=average if average > 0 else None, fills=fills, received_at=now)

    def submit(self, intent: OrderIntent) -> OrderUpdate:
        self._check(intent)
        values = {"symbol": SYMBOL, "side": intent.side, "positionSide": "BOTH",
                  "quantity": format(intent.quantity, "f"), "reduceOnly": str(intent.reduce_only).lower()}
        algo = intent.order_type == "STOP_MARKET"
        if algo:
            values.update(algoType="CONDITIONAL", type="STOP_MARKET", clientAlgoId=intent.intent_id,
                          triggerPrice=format(intent.stop_price, "f"), workingType="MARK_PRICE")
        else:
            values.update(type="MARKET", newClientOrderId=intent.intent_id, newOrderRespType="RESULT")
        try:
            response = self.transport.request("POST", "/fapi/v1/algoOrder" if algo else "/fapi/v1/order",
                                              values, signed=True)
        except DemoApiError as error:
            # Explicit validation/margin rejection means no new order. Timeout, 5xx,
            # duplicate-ID and ambiguous codes still propagate for UNKNOWN reconciliation.
            rejected = {-1100, -1101, -1102, -1111, -1116, -1121, -1130, -2019, -2021, -2022, -4164, -4120}
            if error.code in rejected and (error.http_status is None or 400 <= error.http_status < 500):
                return OrderUpdate(intent_id=intent.intent_id, status="REJECTED", received_at=self.transport.clock())
            raise
        return self._normalize(intent, response, algo=algo)

    def query(self, intent: OrderIntent) -> OrderUpdate | None:
        self._check(intent)
        algo = intent.order_type == "STOP_MARKET"
        parameters = {"clientAlgoId": intent.intent_id} if algo else {"symbol": SYMBOL, "origClientOrderId": intent.intent_id}
        try:
            response = self.transport.request("GET", "/fapi/v1/algoOrder" if algo else "/fapi/v1/order",
                                              parameters, signed=True)
        except DemoApiError as error:
            if error.code == -2013:  # Not found is not safe permission to resubmit.
                return None
            raise
        return self._normalize(intent, response, algo=algo)

    def cancel(self, intent: OrderIntent) -> OrderUpdate:
        self._check(intent)
        algo = intent.order_type == "STOP_MARKET"
        parameters = {"clientAlgoId": intent.intent_id} if algo else {"symbol": SYMBOL, "origClientOrderId": intent.intent_id}
        self.transport.request("DELETE", "/fapi/v1/algoOrder" if algo else "/fapi/v1/order", parameters, signed=True)
        return self.query(intent) or OrderUpdate(intent_id=intent.intent_id, status="UNKNOWN", received_at=self.transport.clock())

    def instrument(self, symbol: str) -> InstrumentRules:
        if symbol != SYMBOL:
            raise ValueError("only BTCUSDT supported for initial Demo rollout")
        response = self.transport.request("GET", "/fapi/v1/exchangeInfo")
        row = next((item for item in response["symbols"] if item["symbol"] == symbol), None)
        if not row or row["status"] != "TRADING" or row["contractType"] != "PERPETUAL":
            raise ExecutionUnavailable("BTCUSDT Perp unavailable in Demo")
        filters = {item["filterType"]: item for item in row["filters"]}
        lot, market = filters["LOT_SIZE"], filters["MARKET_LOT_SIZE"]
        steps = [Decimal(item["stepSize"]) for item in (lot, market) if Decimal(item["stepSize"]) > 0]
        # Decimal LCM supports non-power-of-ten steps without invalid quantities.
        scale = Decimal(10) ** max(-step.as_tuple().exponent for step in steps)
        step = Decimal(math.lcm(*(int(value * scale) for value in steps))) / scale
        return InstrumentRules(
            symbol=symbol, quantity_step=step,
            min_quantity=max(Decimal(lot["minQty"]), Decimal(market["minQty"])),
            max_quantity=min(Decimal(lot["maxQty"]), Decimal(market["maxQty"])),
            min_notional=Decimal(filters["MIN_NOTIONAL"]["notional"]),
            price_tick=Decimal(filters["PRICE_FILTER"]["tickSize"]),
        )

    def quote(self, symbol: str, *, now: datetime) -> Quote:
        if symbol != SYMBOL:
            raise ValueError("unsupported Demo symbol")
        book = self.transport.request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        mark = self.transport.request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        now = self.transport.clock()
        for response in (book, mark):
            age = now.timestamp() - int(response["time"]) / 1000
            if not -2 <= age <= 20:
                raise ExecutionUnavailable("stale Demo execution quote")
        return Quote(bid=book["bidPrice"], ask=book["askPrice"], mark=mark["markPrice"], observed_at=now)

    def account_snapshot(self, *, now: datetime) -> AccountSnapshot:
        request = self.transport.request
        account = request("GET", "/fapi/v3/account", signed=True)
        account_config = request("GET", "/fapi/v1/accountConfig", signed=True)
        permission = account_config.get("canTrade")
        if type(permission) is not bool:
            raise ExecutionUnavailable("Demo trading permission unavailable")
        positions = request("GET", "/fapi/v3/positionRisk", signed=True)
        config = request("GET", "/fapi/v1/symbolConfig", {"symbol": SYMBOL}, signed=True)
        setting = next(item for item in config if item["symbol"] == SYMBOL)
        dual = request("GET", "/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"]
        multi = request("GET", "/fapi/v1/multiAssetsMargin", signed=True)["multiAssetsMargin"]
        orders = request("GET", "/fapi/v1/openOrders", signed=True)
        algos = request("GET", "/fapi/v1/openAlgoOrders", signed=True)
        if type(dual) is not bool or type(multi) is not bool:
            raise ExecutionUnavailable("Demo account modes unavailable")
        return AccountSnapshot(
            account=self.account_ref, wallet_balance=account["totalWalletBalance"],
            equity=account["totalMarginBalance"], available_balance=account["availableBalance"],
            positions=tuple(Position(symbol=row["symbol"], quantity=row["positionAmt"],
                                     entry_price=row["entryPrice"], mark_price=row["markPrice"])
                            for row in positions if Decimal(row["positionAmt"])),
            open_orders=tuple(OpenOrder(symbol=row["symbol"], client_id=row["clientOrderId"], order_type=row["type"])
                              for row in orders) + tuple(OpenOrder(symbol=row["symbol"], client_id=row["clientAlgoId"],
                                                                   order_type=row["orderType"]) for row in algos),
            can_trade=permission, one_way=not dual, single_asset=not multi,
            margin_mode=(setting["marginType"] if setting["isAutoAddMargin"] is False else "AUTO_ADD_MARGIN"),
            leverage=setting["leverage"], observed_at=self.transport.clock(),
        )

    def check_clock(self, *, now: datetime):
        server = self.transport.request("GET", "/fapi/v1/time")
        now = self.transport.clock()
        if abs(now.timestamp() * 1000 - int(server["serverTime"])) > 1000:
            raise ExecutionUnavailable("Demo clock skew exceeds one second; synchronize system time")

    def funding_since(self, since: datetime, *, now: datetime) -> Decimal:
        response = self.transport.request("GET", "/fapi/v1/income", {
            "symbol": SYMBOL, "incomeType": "FUNDING_FEE", "startTime": int(since.timestamp() * 1000),
            "endTime": int(now.timestamp() * 1000), "limit": 1000,
        }, signed=True)
        if len(response) >= 1000 or any(row["asset"] != "USDT" for row in response):
            raise ExecutionUnavailable("funding history requires operator reconciliation")
        return sum((Decimal(row["income"]) for row in response), Decimal(0))
