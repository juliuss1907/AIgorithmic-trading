"""Bounded public Demo/Testnet discovery. Never signs, trades or uses mainnet."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
import json
import math
import threading
import time
from typing import Protocol
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from intraday.assets import ticker_symbol


HOSTS = {
    ("bnb", "spot"): "https://demo-api.binance.com",
    ("bnb", "perp"): "https://demo-fapi.binance.com",
    ("aster", "spot"): "https://sapi.asterdex-testnet.com",
    ("aster", "perp"): "https://fapi.asterdex-testnet.com",
    ("hl", "spot"): "https://api.hyperliquid-testnet.xyz",
    ("hl", "perp"): "https://api.hyperliquid-testnet.xyz",
    ("lighter", "spot"): "https://testnet.zklighter.elliot.ai",
    ("lighter", "perp"): "https://testnet.zklighter.elliot.ai",
}
VENUES = ("bnb", "hl", "aster", "variational", "lighter")


class MarketDiscoveryAdapter(Protocol):
    def discover(self, symbol: str, *, market: str, now: datetime, notional: float = 1000) -> dict: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_json(venue, market, path, params=None, payload=None):
    allowed = {"/info", "/api/v3/exchangeInfo", "/api/v3/ticker/24hr", "/api/v3/depth",
               "/fapi/v1/exchangeInfo", "/fapi/v1/ticker/24hr", "/fapi/v1/depth",
               "/fapi/v3/exchangeInfo", "/fapi/v3/ticker/24hr", "/fapi/v3/depth",
               "/api/v1/orderBooks", "/api/v1/orderBookDetails", "/api/v1/orderBookOrders"}
    if path not in allowed or (payload is not None and (
        venue != "hl" or path != "/info" or payload.get("type") not in {
            "metaAndAssetCtxs", "spotMetaAndAssetCtxs", "l2Book"
        }
    )):
        raise ValueError("invalid public discovery route")
    url = HOSTS[(venue, market)] + path
    if params:
        url += "?" + urlencode(params)
    request = Request(url, data=json.dumps(payload).encode() if payload else None,
                      headers={"Content-Type": "application/json", "User-Agent": "aigt/discovery"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=3) as response:
            body = response.read(2_000_001)
    except HTTPError as error:
        if venue == "bnb" and market == "spot" and path.endswith("exchangeInfo") and error.code == 400:
            code = json.loads(error.read(8192)).get("code")
            if code == -1121:
                return {"symbols": []}
        raise
    if len(body) > 2_000_000:
        raise ValueError("discovery response too large")
    return json.loads(body)


def _number(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError("invalid numeric data")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1e100 or (number and number < 1e-100) or (positive and number == 0):
        raise ValueError("invalid numeric data")
    return number


def book_metrics(bids, asks, *, notional):
    notional = _number(notional, positive=True)
    bids = sorted([(_number(p, positive=True), _number(q)) for p, q, *_ in bids if _number(q)], reverse=True)
    asks = sorted([(_number(p, positive=True), _number(q)) for p, q, *_ in asks if _number(q)])
    if not bids or not asks or bids[0][0] > asks[0][0]:
        raise ValueError("empty or crossed book")
    midpoint = (bids[0][0] + asks[0][0]) / 2
    def impact(levels, buy):
        remaining, quantity, cash = notional, 0., 0.
        for price, size in levels:
            take = min(size, remaining / price)
            cash += take * price
            quantity += take
            remaining -= take * price
            if remaining <= notional * 1e-10:
                return ((cash / quantity / midpoint - 1) if buy else (1 - cash / quantity / midpoint)) * 10_000
        return None
    depth = {
        side: {str(bps): sum(p*q for p,q in levels if abs(p/midpoint-1) <= bps/10_000 + 1e-12)
               for bps in (5,10,25)}
        for side, levels in (("bid", bids), ("ask", asks))
    }
    return {"bid": bids[0][0], "ask": asks[0][0],
            "spread_bps": (asks[0][0]-bids[0][0])/midpoint*10_000,
            "depth_quote": depth, "buy_slippage_bps": impact(asks, True),
            "sell_slippage_bps": impact(bids, False),
            "depth_truncated": any(abs(levels[-1][0]/midpoint-1) < .0025 for levels in (bids,asks))}


class MarketScanner:
    def __init__(self, *, fetch=None, deadline_seconds=10):
        self.fetch = fetch or public_json
        if not 0 < deadline_seconds <= 10:
            raise ValueError("discovery deadline must be at most 10 seconds")
        self.deadline_seconds = deadline_seconds
        self._metadata_cache = {}
        self._cache_lock = threading.Lock()

    def _fetch(self, venue, market, path, params=None, payload=None):
        metadata = path.endswith(("exchangeInfo", "orderBooks")) or payload and payload.get("type") in {"metaAndAssetCtxs", "spotMetaAndAssetCtxs"}
        key = (venue, market, path, json.dumps(params, sort_keys=True), json.dumps(payload, sort_keys=True))
        if metadata:
            with self._cache_lock:
                saved = self._metadata_cache.get(key)
                if saved and time.monotonic() - saved[0] <= 300:
                    return saved[1]
        result = self.fetch(venue, market, path, params, payload=payload)
        if metadata:
            with self._cache_lock:
                if len(self._metadata_cache) >= 256:
                    self._metadata_cache.clear()
                self._metadata_cache[key] = (time.monotonic(), result)
        return result

    def discover(self, symbol, *, market, now, notional=1000):
        symbol = ticker_symbol(symbol)
        if market not in {"spot", "perp"} or now.utcoffset() is None or not 0 < _number(notional) <= 1e8:
            raise ValueError("invalid market, timestamp or scan notional")
        pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="market-scan")
        tasks = {venue: pool.submit(self._venue, venue, symbol, market, now, notional) for venue in VENUES}
        done, _ = wait(tasks.values(), timeout=self.deadline_seconds)
        rows = [tasks[v].result() if tasks[v] in done else self._empty(v,symbol,market,now,"unavailable","scan_deadline") for v in VENUES]
        pool.shutdown(wait=False, cancel_futures=True)
        return {"symbol": symbol, "market": market, "notional_quote": notional,
                "created_at": now.isoformat(), "venues": rows}

    @staticmethod
    def _empty(venue, symbol, market, now, status="unlisted", reason=None):
        return {"venue":venue, "environment":"demo" if venue=="bnb" else "testnet", "market":market,
                "symbol":symbol, "instrument":None, "base_asset":symbol[:-4], "quote_asset":None,
                "status":status, "reason":reason, "execution_available":venue=="bnb", "selectable":False,
                "volume_24h_quote":None, "bid":None, "ask":None, "spread_bps":None, "depth_quote":None,
                "buy_slippage_bps":None, "sell_slippage_bps":None, "source_timestamp":None,
                "received_at":now.isoformat(), "warnings":[]}

    def _venue(self, venue, symbol, market, now, notional):
        row = self._empty(venue,symbol,market,now)
        if venue == "variational":
            return {**row,"status":"testnet_unverified","reason":"no_verified_public_testnet_api"}
        try:
            if venue in {"bnb","aster"}:
                self._binance_style(row, notional)
            elif venue == "hl":
                self._hyperliquid(row, notional)
            elif venue == "lighter":
                self._lighter(row, notional)
        except (Exception,):
            return {**row, "status":"unavailable", "selectable":False, "reason":"public_api_or_data_unavailable"}
        return row

    def _liquidity(self,row,bids,asks,notional):
        try:
            row.update(book_metrics(bids,asks,notional=notional))
            if row["depth_truncated"]:
                row["warnings"].append("depth_is_snapshot_lower_bound")
            if row["buy_slippage_bps"] is None or row["sell_slippage_bps"] is None:
                row["warnings"].append("insufficient_depth_for_selected_size")
        except (ValueError, TypeError):
            row["warnings"].append("liquidity_unavailable")

    def _binance_style(self,row,notional):
        venue, market, symbol = row["venue"],row["market"],row["symbol"]
        prefix = "/api/v3" if market=="spot" else "/fapi/v3" if venue=="aster" else "/fapi/v1"
        metadata = self._fetch(venue,market,prefix+"/exchangeInfo", {"symbol":symbol} if venue=="bnb" and market=="spot" else None)
        matches = [s for s in metadata["symbols"] if s["symbol"]==symbol and s["baseAsset"]==symbol[:-4] and s["quoteAsset"]=="USDT"]
        if len(matches)!=1:
            row.update(status="ambiguous" if matches else "unlisted")
            return
        instrument = matches[0]
        if instrument["status"]!="TRADING" or (market=="perp" and instrument.get("contractType")!="PERPETUAL"):
            row.update(status="unsupported",reason="market_not_trading_or_outside_scope")
            return
        orders = instrument.get("orderTypes",[])
        protected = "MARKET" in orders and ("STOP_LOSS" if market=="spot" else "STOP_MARKET") in orders
        row.update(status="listed",instrument=symbol,quote_asset="USDT",order_types=orders,
                   selectable=venue=="bnb" and protected,
                   reason=None if venue=="bnb" and protected else "execution_adapter_unavailable" if venue!="bnb" else "native_protection_unavailable")
        ticker = self.fetch(venue,market,prefix+"/ticker/24hr",{"symbol":symbol})
        if ticker.get("symbol")!=symbol:
            raise ValueError("ticker symbol mismatch")
        row["volume_24h_quote"] = _number(ticker["quoteVolume"]) if ticker.get("quoteVolume") is not None else None
        book = self.fetch(venue,market,prefix+"/depth",{"symbol":symbol,"limit":100})
        self._liquidity(row,book["bids"],book["asks"],notional)

    def _hyperliquid(self,row,notional):
        market, base = row["market"],row["base_asset"]
        fetch = lambda payload: self._fetch("hl",market,"/info",payload=payload)
        meta, contexts = fetch({"type":"metaAndAssetCtxs" if market=="perp" else "spotMetaAndAssetCtxs"})
        if market=="perp":
            matches=[(i,s) for i,s in enumerate(meta["universe"]) if s["name"]==base and not s.get("isDelisted",False)]
        else:
            tokens={t["index"]:t for t in meta["tokens"]}
            matches=[(i,s) for i,s in enumerate(meta["universe"]) if tokens[s["tokens"][0]]["name"]==base]
        if len(matches)!=1:
            row.update(status="ambiguous" if matches else "unlisted")
            return
        index, instrument = matches[0]
        quote = "USDC" if market=="perp" else tokens[instrument["tokens"][1]]["name"]
        row.update(status="listed",instrument=instrument["name"],quote_asset=quote,reason="execution_adapter_unavailable")
        row["volume_24h_quote"] = _number(contexts[index]["dayNtlVlm"])
        book=fetch({"type":"l2Book","coin":instrument["name"]})
        if book["coin"]!=instrument["name"]:
            raise ValueError("book identity mismatch")
        row["source_timestamp"]=datetime.fromtimestamp(int(book["time"])/1000,timezone.utc).isoformat()
        self._liquidity(row,[[r["px"],r["sz"]] for r in book["levels"][0]],[[r["px"],r["sz"]] for r in book["levels"][1]],notional)

    def _lighter(self,row,notional):
        market,base=row["market"],row["base_asset"]
        fetch=lambda path,params=None:self._fetch("lighter",market,"/api/v1/"+path,params)
        meta=fetch("orderBooks")
        matches=[s for s in meta["order_books"] if s["market_type"]==market and s["symbol"].split("/")[0]==base and s["status"]=="active"]
        if len(matches)!=1:
            row.update(status="ambiguous" if matches else "unlisted")
            return
        instrument=matches[0]
        row.update(status="listed",instrument=str(instrument["market_id"]),quote_asset=instrument["symbol"].split("/")[1] if "/" in instrument["symbol"] else "USD",
                   reason="execution_adapter_unavailable")
        details=fetch("orderBookDetails",{"market_id":instrument["market_id"]})
        details=details["spot_order_book_details" if market=="spot" else "order_book_details"]
        detail=next(d for d in details if d["market_id"]==instrument["market_id"])
        row["volume_24h_quote"]=_number(detail["daily_quote_token_volume"])
        book=fetch("orderBookOrders",{"market_id":instrument["market_id"],"limit":100})
        self._liquidity(row,[[r["price"],r["remaining_base_amount"]] for r in book["bids"]],
                        [[r["price"],r["remaining_base_amount"]] for r in book["asks"]],notional)
