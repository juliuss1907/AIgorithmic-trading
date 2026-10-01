"""Spot Demo adapter. Explicit balances, actual trades and native sell protection.

Balances are not strategy inventory: ownership is determined by journaled fills.
This adapter never borrows, changes permissions or adopts pre-existing holdings.
"""

from datetime import datetime, timezone
from decimal import Decimal
import math

from intraday.assets import normalize_symbol
from intraday.execution.binance_demo import DemoApiError, DemoTransport
from intraday.execution.contracts import (
    ExecutionFill, ExecutionUnavailable, InstrumentRules, OpenOrder, OrderIntent,
    OrderUpdate, Quote, SpotAccountSnapshot, SpotBalance,
)


class BinanceSpotDemoAdapter:
    def __init__(self, transport: DemoTransport, *, symbol: str):
        if transport.market != "spot":
            raise ValueError("Spot adapter requires Spot Demo transport")
        self.transport = transport
        self.symbol = normalize_symbol(symbol)
        self.account_ref = transport.credentials.account_ref.model_copy(update={"market": "spot"})

    def _check(self, intent):
        if (intent.account != self.account_ref or intent.market != "spot"
                or intent.symbol != self.symbol or intent.reduce_only
                or intent.order_type not in {"MARKET", "STOP_LOSS"}):
            raise ValueError("Spot Demo symbol, market, order type or account mismatch")

    def _normalize(self, intent, response):
        self._check(intent)
        if (response.get("clientOrderId") != intent.intent_id or response.get("symbol") != self.symbol
                or response.get("side") != intent.side or response.get("type") != intent.order_type
                or Decimal(response.get("origQty", "0")) != intent.quantity
                or intent.stop_price is not None and Decimal(response.get("stopPrice", "0")) != intent.stop_price):
            raise ExecutionUnavailable("Spot Demo order differs from persisted intent")
        executed = Decimal(response.get("executedQty", "0"))
        if executed > intent.quantity:
            raise ExecutionUnavailable("Spot Demo executed quantity exceeds intent")
        fills = ()
        if executed:
            trades = self.transport.request("GET", "/api/v3/myTrades",
                                            {"symbol": self.symbol, "orderId": response["orderId"], "limit": 1000}, signed=True)
            if len(trades) >= 1000:
                raise ExecutionUnavailable("Spot fill history requires operator reconciliation")
            if any(row.get("symbol") != self.symbol or str(row.get("orderId")) != str(response["orderId"])
                   or type(row.get("isBuyer")) is not bool or row["isBuyer"] != (intent.side == "BUY") for row in trades):
                raise ExecutionUnavailable("Spot fill identity mismatch")
            fills = tuple(ExecutionFill(
                fill_id=f"{self.symbol}:{row['id']}", quantity=row["qty"], price=row["price"],
                commission=row["commission"], commission_asset=row["commissionAsset"],
                filled_at=datetime.fromtimestamp(int(row["time"])/1000, timezone.utc),
            ) for row in trades)
            if sum((f.quantity for f in fills), Decimal(0)) != executed:
                raise ExecutionUnavailable("Spot trade history does not yet confirm all executions")
        status = response.get("status", "UNKNOWN")
        if status not in {"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
            status = "UNKNOWN"
        average = sum((f.price*f.quantity for f in fills), Decimal(0))/executed if executed else None
        return OrderUpdate(intent_id=intent.intent_id, status=status, exchange_order_id=str(response["orderId"]),
                           executed_quantity=executed, average_price=average, fills=fills, received_at=self.transport.clock())

    def submit(self, intent: OrderIntent):
        self._check(intent)
        values = {"symbol": self.symbol, "side": intent.side, "type": intent.order_type,
                  "quantity": format(intent.quantity, "f"), "newClientOrderId": intent.intent_id,
                  "newOrderRespType": "FULL"}
        if intent.stop_price is not None:
            values["stopPrice"] = format(intent.stop_price, "f")
        try:
            response = self.transport.request("POST", "/api/v3/order", values, signed=True)
        except DemoApiError as error:
            if error.code in {-1013, -1100, -1101, -1102, -1111, -1116, -1121, -1130, -2010} and (
                    error.http_status is None or 400 <= error.http_status < 500):
                # -2010 includes duplicate client ID; ambiguous acceptance must reconcile.
                if error.code == -2010:
                    raise
                return OrderUpdate(intent_id=intent.intent_id, status="REJECTED", received_at=self.transport.clock())
            raise
        return self._normalize(intent, response)

    def query(self, intent):
        self._check(intent)
        try:
            result = self.transport.request("GET", "/api/v3/order",
                                            {"symbol": self.symbol, "origClientOrderId": intent.intent_id}, signed=True)
        except DemoApiError as error:
            if error.code == -2013:
                return None
            raise
        return self._normalize(intent, result)

    def cancel(self, intent):
        self._check(intent)
        self.transport.request("DELETE", "/api/v3/order",
                               {"symbol": self.symbol, "origClientOrderId": intent.intent_id}, signed=True)
        return self.query(intent) or OrderUpdate(intent_id=intent.intent_id, status="UNKNOWN", received_at=self.transport.clock())

    def instrument(self, symbol, *, order_type="MARKET"):
        if symbol != self.symbol or order_type not in {"MARKET", "STOP_LOSS"}:
            raise ValueError("Spot Demo instrument mismatch")
        metadata = self.transport.request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        matches = [row for row in metadata["symbols"] if row.get("symbol") == symbol]
        if len(matches) != 1:
            raise ExecutionUnavailable("Spot Demo instrument unavailable")
        row = matches[0]
        if (row.get("status") != "TRADING" or row.get("baseAsset") != symbol[:-4]
                or row.get("quoteAsset") != "USDT" or row.get("isSpotTradingAllowed") is not True
                or not {"MARKET", "STOP_LOSS"} <= set(row.get("orderTypes", []))):
            raise ExecutionUnavailable("Spot Demo trading/protection unavailable")
        filters = {f["filterType"]: f for f in row["filters"]}
        lots = [filters["LOT_SIZE"]]
        if order_type == "MARKET" and "MARKET_LOT_SIZE" in filters:
            lots.append(filters["MARKET_LOT_SIZE"])
        steps = [Decimal(f["stepSize"]) for f in lots if Decimal(f["stepSize"]) > 0]
        scale = Decimal(10) ** max(-s.as_tuple().exponent for s in steps)
        step = Decimal(math.lcm(*(int(s*scale) for s in steps)))/scale
        minimums, maximums = [], []
        for kind in ("MIN_NOTIONAL", "NOTIONAL"):
            f = filters.get(kind)
            if not f:
                continue
            enabled = order_type != "MARKET" or f.get("applyToMarket" if kind == "MIN_NOTIONAL" else "applyMinToMarket") is True
            if enabled:
                minimums.append(Decimal(f["minNotional"]))
            if kind == "NOTIONAL" and (order_type != "MARKET" or f.get("applyMaxToMarket") is True):
                if Decimal(f["maxNotional"]) > 0:
                    maximums.append(Decimal(f["maxNotional"]))
        return InstrumentRules(symbol=symbol, quantity_step=step,
                               min_quantity=max(Decimal(f["minQty"]) for f in lots),
                               max_quantity=min(Decimal(f["maxQty"]) for f in lots if Decimal(f["maxQty"]) > 0),
                               price_tick=filters["PRICE_FILTER"]["tickSize"],
                               min_price=filters["PRICE_FILTER"].get("minPrice",0),
                               max_price=Decimal(filters["PRICE_FILTER"].get("maxPrice","0")) or None,
                               min_notional=max(minimums, default=Decimal(0)),
                               max_notional=min(maximums) if maximums else None)

    def account_snapshot(self, *, now):
        response = self.transport.request("GET", "/api/v3/account", signed=True)
        orders = self.transport.request("GET", "/api/v3/openOrders", signed=True)
        if type(response.get("canTrade")) is not bool or response.get("accountType") != "SPOT":
            raise ExecutionUnavailable("Spot Demo permissions unavailable")
        return SpotAccountSnapshot(account=self.account_ref, can_trade=response["canTrade"],
                                   balances=tuple(SpotBalance(asset=b["asset"], free=b["free"], locked=b["locked"])
                                                  for b in response["balances"]),
                                   open_orders=tuple(OpenOrder(symbol=o["symbol"], client_id=o["clientOrderId"], order_type=o["type"])
                                                     for o in orders), observed_at=self.transport.clock())

    def check_clock(self, *, now):
        result = self.transport.request("GET", "/api/v3/time")
        if abs(self.transport.clock().timestamp()*1000-int(result["serverTime"])) > 1000:
            raise ExecutionUnavailable("Spot Demo clock skew exceeds one second")

    def quote(self, symbol, *, now):
        if symbol != self.symbol:
            raise ValueError("Spot Demo quote symbol mismatch")
        started = self.transport.clock()
        response = self.transport.request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})
        received = self.transport.clock()
        if response.get("symbol") != symbol or not 0 <= (received-started).total_seconds() <= 3:
            raise ExecutionUnavailable("Spot Demo quote identity or freshness unavailable")
        bid, ask = Decimal(response["bidPrice"]), Decimal(response["askPrice"])
        return Quote(bid=bid, ask=ask, mark=(bid+ask)/2, observed_at=received)
