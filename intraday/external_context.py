"""Read-only collectors for slow context and shadow perp-DEX evidence."""

from __future__ import annotations

import json
import asyncio
import threading
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from intraday.assets import ASSET_REGISTRY, asset_spec
from intraday.contracts import ExternalObservation


USER_AGENT = "system-trading-lab/0.1 local-paper-research"
CRYPTORANK_BASE_URL = "https://api.cryptorank.io/v3"
ASTER_BASE_URL = "https://fapi.asterdex.com"
VARIATIONAL_STATS_URL = (
    "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats"
)
LIGHTER_WS_URL = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"
ASTER_WS_URL_TEMPLATE = "wss://fstream.asterdex.com/ws/{stream}@forceOrder"


def _registered_symbols(symbols: tuple[str, ...] | None) -> tuple[str, ...]:
    requested = symbols or tuple(ASSET_REGISTRY)
    return tuple(asset_spec(symbol).symbol for symbol in requested)


def _timestamp(value, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        divisor = 1000 if value > 10_000_000_000 else 1
        return datetime.fromtimestamp(value / divisor, tz=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _number(value, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise ValueError(f"{name} must be finite")
    return result


def _payload_data(payload: dict) -> dict:
    data = payload.get("data", payload)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise ValueError("API data must be an object")
    return data


def _first(data: dict, *names, default=None):
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


def parse_cryptorank_context(
    *, fear_greed: dict, altcoin: dict, global_market: dict, received_at: datetime
) -> ExternalObservation:
    fear = _payload_data(fear_greed)
    alt = _payload_data(altcoin)
    market = _payload_data(global_market)
    status = fear_greed.get("status")
    status = status if isinstance(status, dict) else {}
    source_time = _timestamp(
        _first(status, "timestamp", default=_first(fear, "timestamp", "updatedAt", "updated_at")),
        received_at,
    )
    return ExternalObservation.create(
        source="cryptorank",
        dataset="market_context",
        symbol=None,
        source_timestamp=source_time,
        received_at=received_at,
        metrics={
            "fear_greed_value": _number(_first(fear, "currentValue", "value", "index"), name="fear_greed_value"),
            "altcoin_index_value": _number(_first(alt, "currentValue", "value", "index"), name="altcoin_index_value"),
            "total_market_cap": _number(_first(market, "totalMarketCap", "marketCap", "market_cap"), name="total_market_cap"),
            "total_volume_24h": _number(_first(market, "totalVolume24h", "volume24h", "volume_24h"), name="total_volume_24h"),
            "market_cap_change_24h": _number(_first(market, "marketCapChangePercent24h", "marketCapChange24h", "market_cap_change_24h"), name="market_cap_change_24h"),
        },
        labels={
            "fear_greed_classification": str(_first(fear, "classification", "name", default="unknown")),
            "altcoin_index_classification": str(_first(alt, "classification", "name", default="unknown")),
        },
    )


def _quote_spread_bps(quote: dict | None) -> float | None:
    if not isinstance(quote, dict):
        return None
    bid = _number(quote.get("bid"), name="quote bid")
    ask = _number(quote.get("ask"), name="quote ask")
    if bid <= 0 or ask < bid:
        raise ValueError("quote is crossed or non-positive")
    return (ask - bid) / ((ask + bid) / 2) * 10_000


def parse_variational_stats(
    payload: dict, *, symbol: str = "BTCUSDT", received_at: datetime
) -> ExternalObservation:
    spec = asset_spec(symbol)
    listings = payload.get("listings")
    if not isinstance(listings, list):
        raise ValueError("Variational listings are missing")
    listing = next(
        (item for item in listings if item.get("ticker") == spec.variational_ticker),
        None,
    )
    if not isinstance(listing, dict):
        raise ValueError(f"Variational {spec.variational_ticker} listing is missing")
    interest = listing.get("open_interest")
    quotes = listing.get("quotes")
    if not isinstance(interest, dict) or not isinstance(quotes, dict):
        raise ValueError("Variational BTC OI or quotes are missing")
    interval = _number(listing.get("funding_interval_s"), name="funding_interval_s")
    funding = _number(listing.get("funding_rate"), name="funding_rate")
    return ExternalObservation.create(
        source="variational", dataset="perp_market", symbol=spec.symbol,
        source_timestamp=_timestamp(quotes.get("updated_at"), received_at), received_at=received_at,
        metrics={
            "mark_price": _number(listing.get("mark_price"), name="mark_price"),
            "volume_24h_usd": _number(listing.get("volume_24h"), name="volume_24h"),
            "long_open_interest_usd": _number(interest.get("long_open_interest"), name="long_open_interest"),
            "short_open_interest_usd": _number(interest.get("short_open_interest"), name="short_open_interest"),
            "funding_rate": funding,
            "funding_bps_hour": funding * 10_000 * 3600 / interval,
            "base_spread_bps": _number(listing.get("base_spread_bps"), name="base_spread_bps"),
            "quote_spread_1k_bps": _quote_spread_bps(quotes.get("size_1k")),
            "quote_spread_100k_bps": _quote_spread_bps(quotes.get("size_100k")),
            "quote_spread_1m_bps": _quote_spread_bps(quotes.get("size_1m")),
            "venue_tvl_usd": _number(payload.get("tvl"), name="tvl"),
        }, labels={},
    )


def parse_aster_snapshot(
    *, symbol: str = "BTCUSDT", premium: dict, book: dict,
    liquidation: dict | None, received_at: datetime
) -> ExternalObservation:
    spec = asset_spec(symbol)
    if premium.get("symbol", spec.aster_symbol) != spec.aster_symbol:
        raise ValueError(f"Aster response is not {spec.aster_symbol}")
    mark = _number(premium.get("markPrice"), name="markPrice")
    index = _number(premium.get("indexPrice"), name="indexPrice")
    bids, asks = book.get("bids"), book.get("asks")
    if not bids or not asks:
        raise ValueError("Aster book must contain both sides")
    bid, ask = _number(bids[0][0], name="bid"), _number(asks[0][0], name="ask")
    if bid > ask:
        raise ValueError("Aster book is crossed")
    metrics: dict[str, float | None] = {
        "mark_price": mark, "index_price": index,
        "basis_bps": (mark / index - 1) * 10_000,
        "funding_rate": _number(premium.get("lastFundingRate"), name="lastFundingRate"),
        "best_bid": bid, "best_ask": ask,
        "spread_bps": (ask - bid) / ((ask + bid) / 2) * 10_000,
        "bid_depth_usd": sum(_number(p, name="bid price") * _number(q, name="bid qty") for p, q, *_ in bids),
        "ask_depth_usd": sum(_number(p, name="ask price") * _number(q, name="ask qty") for p, q, *_ in asks),
        "liquidation_notional_usd": None,
    }
    labels = {}
    source_time = _timestamp(_first(premium, "time", default=book.get("E")), received_at)
    if liquidation is not None:
        order = liquidation.get("o")
        liquidation_time = _timestamp(liquidation.get("E"), received_at)
        liquidation_age = (received_at - liquidation_time).total_seconds()
        if (
            isinstance(order, dict)
            and order.get("s") == spec.aster_symbol
            and -1 <= liquidation_age <= 300
        ):
            price = _number(_first(order, "ap", "p"), name="liquidation price")
            quantity = _number(_first(order, "z", "q"), name="liquidation quantity")
            metrics["liquidation_notional_usd"] = price * quantity
            metrics["liquidation_age_seconds"] = liquidation_age
            labels["liquidation_side"] = str(order.get("S", "unknown"))
            source_time = max(source_time, liquidation_time)
    return ExternalObservation.create(
        source="aster", dataset="perp_market", symbol=spec.symbol,
        source_timestamp=source_time, received_at=received_at, metrics=metrics, labels=labels,
    )


def parse_lighter_market_stats(
    payload: dict, *, symbol: str = "BTCUSDT", received_at: datetime
) -> ExternalObservation:
    spec = asset_spec(symbol)
    stats = payload.get("market_stats")
    if (
        not isinstance(stats, dict)
        or stats.get("symbol") != spec.base_asset
        or int(stats.get("market_id", -1)) != spec.lighter_market_id
    ):
        raise ValueError(f"Lighter {spec.base_asset} market stats are missing")
    bid = _number(stats.get("best_bid_price"), name="best_bid_price")
    ask = _number(stats.get("best_ask_price"), name="best_ask_price")
    if bid > ask:
        raise ValueError("Lighter book is crossed")
    return ExternalObservation.create(
        source="lighter", dataset="perp_market", symbol=spec.symbol,
        source_timestamp=_timestamp(payload.get("timestamp"), received_at), received_at=received_at,
        metrics={
            "market_id": _number(stats.get("market_id"), name="market_id"),
            "mark_price": _number(stats.get("mark_price"), name="mark_price"),
            "index_price": _number(stats.get("index_price"), name="index_price"),
            "open_interest_base": _number(stats.get("open_interest"), name="open_interest"),
            "open_interest_usd": _number(stats.get("open_interest"), name="open_interest")
            * _number(stats.get("mark_price"), name="mark_price"),
            "estimated_funding_rate": _number(stats.get("current_funding_rate"), name="current_funding_rate"),
            "last_funding_rate": _number(stats.get("funding_rate"), name="funding_rate"),
            "spread_bps": (ask - bid) / ((ask + bid) / 2) * 10_000,
            "volume_24h_usd": _number(stats.get("daily_quote_token_volume"), name="daily_quote_token_volume"),
            "premium_pct": _number(stats.get("premium"), name="premium"),
        }, labels={},
    )


def _get_json(url: str, *, headers: dict | None = None, timeout_seconds: float = 10):
    request = Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.load(response)


class CryptoRankCollector:
    def __init__(self, api_key: str, fetch_json=_get_json):
        self.api_key, self.fetch_json = api_key, fetch_json

    def __call__(self, now: datetime) -> ExternalObservation:
        headers = {"X-Api-Key": self.api_key}
        fetch = lambda path: self.fetch_json(f"{CRYPTORANK_BASE_URL}{path}", headers=headers)
        return parse_cryptorank_context(
            fear_greed=fetch("/global/fear-greed"),
            altcoin=fetch("/global/altcoin-index"),
            global_market=fetch("/global/market"), received_at=now,
        )


class VariationalCollector:
    def __init__(
        self,
        fetch_json=_get_json,
        *,
        symbols: tuple[str, ...] | None = None,
    ):
        self.fetch_json = fetch_json
        self.symbols = _registered_symbols(symbols)

    def __call__(self, now: datetime) -> tuple[ExternalObservation, ...]:
        payload = self.fetch_json(VARIATIONAL_STATS_URL)
        return tuple(
            parse_variational_stats(payload, symbol=symbol, received_at=now)
            for symbol in self.symbols
        )


class AsterLiquidationFeed:
    def __init__(self, *, symbols: tuple[str, ...] | None = None):
        self.symbols = _registered_symbols(symbols)
        self._lock, self._latest = threading.Lock(), {}
        self._stop = threading.Event()

    def update(self, payload: dict) -> None:
        order = payload.get("o")
        if payload.get("e") != "forceOrder" or not isinstance(order, dict):
            return
        try:
            symbol = asset_spec(str(order.get("s", ""))).symbol
        except ValueError:
            return
        if symbol in self.symbols:
            with self._lock:
                self._latest[symbol] = payload

    def latest(self, symbol: str = "BTCUSDT") -> dict | None:
        with self._lock:
            return self._latest.get(asset_spec(symbol).symbol)

    async def _run(self, symbol: str) -> None:
        from websockets.asyncio.client import connect

        stream = asset_spec(symbol).aster_symbol.lower()
        url = ASTER_WS_URL_TEMPLATE.format(stream=stream)
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with connect(url, open_timeout=10, ping_interval=20) as socket:
                    backoff = 1.0
                    async for raw in socket:
                        self.update(json.loads(raw))
                        if self._stop.is_set():
                            break
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def start(self) -> None:
        for symbol in self.symbols:
            threading.Thread(
                target=lambda current=symbol: asyncio.run(self._run(current)),
                daemon=True,
            ).start()

    def stop(self) -> None:
        self._stop.set()


class AsterCollector:
    def __init__(
        self,
        feed: AsterLiquidationFeed | None = None,
        fetch_json=_get_json,
        *,
        symbols: tuple[str, ...] | None = None,
    ):
        self.feed, self.fetch_json = feed, fetch_json
        self.symbols = _registered_symbols(symbols)

    def __call__(self, now: datetime) -> tuple[ExternalObservation, ...]:
        observations = []
        for symbol in self.symbols:
            spec = asset_spec(symbol)
            query = urlencode({"symbol": spec.aster_symbol})
            premium = self.fetch_json(f"{ASTER_BASE_URL}/fapi/v3/premiumIndex?{query}")
            book = self.fetch_json(f"{ASTER_BASE_URL}/fapi/v3/depth?{query}&limit=100")
            observations.append(parse_aster_snapshot(
                symbol=symbol,
                premium=premium,
                book=book,
                liquidation=self.feed.latest(symbol) if self.feed else None,
                received_at=now,
            ))
        return tuple(observations)


class LighterCollector:
    def __init__(self, *, symbols: tuple[str, ...] | None = None):
        self.symbols = _registered_symbols(symbols)

    @staticmethod
    def _collect_symbol(socket, symbol: str, now: datetime) -> ExternalObservation:
        spec = asset_spec(symbol)
        socket.send(json.dumps({
            "type": "subscribe",
            "channel": f"market_stats/{spec.lighter_market_id}",
        }))
        for _ in range(3):
            payload = json.loads(socket.recv(timeout=10))
            stats = payload.get("market_stats")
            if (
                isinstance(stats, dict)
                and stats.get("symbol") == spec.base_asset
                and int(stats.get("market_id", -1)) == spec.lighter_market_id
            ):
                return parse_lighter_market_stats(
                    payload, symbol=symbol, received_at=now
                )
        raise ValueError(f"Lighter {spec.base_asset} market stats were not received")

    def __call__(self, now: datetime) -> tuple[ExternalObservation, ...]:
        from websockets.sync.client import connect

        observations = []
        for symbol in self.symbols:
            with connect(LIGHTER_WS_URL, open_timeout=10, close_timeout=2) as socket:
                observations.append(self._collect_symbol(socket, symbol, now))
        return tuple(observations)


def run_external_context_cycle(store, *, collectors: dict[str, Callable], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    recorded, failed = [], []
    for source, collector in collectors.items():
        try:
            result = collector(now)
            observations = (result,) if isinstance(result, ExternalObservation) else tuple(result)
            if not observations:
                raise ValueError("collector returned no observations")
            for observation in observations:
                if observation.source != source:
                    raise ValueError("collector returned the wrong source")
                store.record_external_observation(observation)
            recorded.append(source)
        except Exception:
            failed.append(source)
    return {"recorded": recorded, "failed": failed}
