"""Binance USD-M public market data and deterministic feature engineering."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from intraday.contracts import FeatureSnapshot


PUBLIC_BASE_URL = "https://fapi.binance.com"
ALLOWED_PATHS = {
    "/fapi/v1/klines",
    "/fapi/v1/depth",
    "/fapi/v1/premiumIndex",
    "/fapi/v1/openInterest",
    "/futures/data/globalLongShortAccountRatio",
}


def _ema(values: list[float], period: int) -> float:
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _ema_series(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def compute_indicators(rows: list[list]) -> dict[str, float]:
    """Compute the canonical indicator set from Binance-format candles."""
    if len(rows) < 35:
        raise ValueError("at least 35 candles are required")
    closes = [float(row[4]) for row in rows]
    volumes = [float(row[5]) for row in rows]
    taker_buy_volumes = [float(row[9]) for row in rows]

    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    window = changes[-14:]
    average_gain = fmean(max(change, 0) for change in window)
    average_loss = fmean(max(-change, 0) for change in window)
    rsi = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)

    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_series = [fast - slow for fast, slow in zip(ema12, ema26)]
    macd = macd_series[-1]
    macd_signal = _ema(macd_series, 9)

    bollinger = closes[-20:]
    bb_mid = fmean(bollinger)
    deviation = pstdev(bollinger)
    traveled = sum(abs(current - previous) for previous, current in zip(bollinger, bollinger[1:]))
    efficiency = abs(bollinger[-1] - bollinger[0]) / traveled if traveled else 0.0
    volume = volumes[-1]
    buy_volume = taker_buy_volumes[-1]

    return {
        "price": closes[-1],
        "volume_1h": volume,
        "rsi14": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd - macd_signal,
        "bb_mid": bb_mid,
        "bb_upper": bb_mid + 2 * deviation,
        "bb_lower": bb_mid - 2 * deviation,
        "buy_ratio": buy_volume / volume if volume else 0.0,
        "path_efficiency": efficiency,
    }


def build_feature_snapshot(
    *,
    symbol: str,
    candles: list[list],
    book: dict,
    premium: dict,
    open_interest: dict | None,
    long_short: dict | None,
    event_time: datetime,
    built_at: datetime,
    sentiment_score: float | None,
) -> FeatureSnapshot:
    indicators = compute_indicators(candles)
    bids = [(float(price), float(quantity)) for price, quantity, *_ in book["bids"][:20]]
    asks = [(float(price), float(quantity)) for price, quantity, *_ in book["asks"][:20]]
    if not bids or not asks:
        raise ValueError("order book must contain both bids and asks")
    bid, ask = bids[0][0], asks[0][0]
    bid_quantity = sum(quantity for _, quantity in bids)
    ask_quantity = sum(quantity for _, quantity in asks)
    total_quantity = bid_quantity + ask_quantity
    mark_price = float(premium["markPrice"])
    index_price = float(premium["indexPrice"])
    candle_close = float(indicators["price"])
    midpoint = (bid + ask) / 2
    candle_closed_at = datetime.fromtimestamp(
        int(candles[-1][6]) / 1000, tz=timezone.utc
    )

    features: dict[str, float | None] = {
        **indicators,
        "reference_price": mark_price,
        "candle_close_price": candle_close,
        "reference_to_close_bps": (mark_price / candle_close - 1) * 10_000,
        "reference_mid_dislocation_bps": (mark_price / midpoint - 1) * 10_000,
        "closed_candle_age_seconds": max(
            0.0, (event_time - candle_closed_at).total_seconds()
        ),
        "mark_price": mark_price,
        "index_price": index_price,
        "basis_bps": (mark_price / index_price - 1) * 10_000,
        "funding_rate": float(premium["lastFundingRate"]),
        "open_interest": (
            float(open_interest["openInterest"]) if open_interest is not None else None
        ),
        "long_short_ratio": (
            float(long_short["longShortRatio"]) if long_short is not None else None
        ),
        "order_book_imbalance": (
            (bid_quantity - ask_quantity) / total_quantity if total_quantity else 0.0
        ),
        "spread_bps": (ask - bid) / ((ask + bid) / 2) * 10_000,
        "sentiment_score": sentiment_score,
    }
    freshness = {
        "candles": True,
        "order_book": True,
        "premium": True,
        "open_interest": open_interest is not None,
        "long_short_ratio": long_short is not None,
        "sentiment": sentiment_score is not None,
    }
    flags = tuple(f"{name}_missing" for name, fresh in freshness.items() if not fresh)
    if not all(math.isfinite(value) for value in features.values() if value is not None):
        raise ValueError("features must be finite")
    return FeatureSnapshot.create(
        symbol=symbol,
        market="binance_usdm_perp",
        timeframe="1h",
        feature_schema_version="2",
        event_time=event_time,
        built_at=built_at,
        bid=bid,
        ask=ask,
        features=features,
        freshness=freshness,
        quality_flags=flags,
    )


class BinanceUsdMClient:
    """Small read-only adapter for allowlisted Binance USD-M public endpoints."""

    def __init__(
        self,
        *,
        fetch_json: Callable[[str, dict], object] | None = None,
        timeout_seconds: float = 10,
    ):
        self.timeout_seconds = timeout_seconds
        self._fetch_json = fetch_json or self._http_get

    def _http_get(self, path: str, params: dict):
        if path not in ALLOWED_PATHS:
            raise ValueError("endpoint is not allowlisted")
        request = Request(
            f"{PUBLIC_BASE_URL}{path}?{urlencode(params)}",
            headers={"User-Agent": "system-trading-lab/0.1"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.load(response)

    @staticmethod
    def _symbol(symbol: str) -> str:
        if symbol != "BTCUSDT":
            raise ValueError("v1 only permits BTCUSDT")
        return symbol

    def candles(self, symbol: str = "BTCUSDT", *, interval: str = "1m", limit: int = 100):
        if interval not in {"1m", "5m", "15m", "1h"}:
            raise ValueError("unsupported interval")
        if not 35 <= limit <= 1000:
            raise ValueError("limit must be between 35 and 1000")
        return self._fetch_json(
            "/fapi/v1/klines",
            {"symbol": self._symbol(symbol), "interval": interval, "limit": limit},
        )

    def order_book(self, symbol: str = "BTCUSDT", *, limit: int = 20):
        if limit not in {5, 10, 20, 50, 100, 500, 1000}:
            raise ValueError("unsupported depth limit")
        return self._fetch_json(
            "/fapi/v1/depth", {"symbol": self._symbol(symbol), "limit": limit}
        )

    def premium(self, symbol: str = "BTCUSDT"):
        return self._fetch_json("/fapi/v1/premiumIndex", {"symbol": self._symbol(symbol)})

    def open_interest(self, symbol: str = "BTCUSDT"):
        return self._fetch_json("/fapi/v1/openInterest", {"symbol": self._symbol(symbol)})

    def long_short_ratio(self, symbol: str = "BTCUSDT", *, period: str = "5m"):
        result = self._fetch_json(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": self._symbol(symbol), "period": period, "limit": 1},
        )
        return result[-1] if result else None

    def snapshot(
        self,
        symbol: str = "BTCUSDT",
        *,
        now: datetime | None = None,
        sentiment_score: float | None = 0.0,
    ) -> FeatureSnapshot:
        """Collect one internally consistent REST snapshot for the decision loop."""
        now = now or datetime.now(timezone.utc)
        return build_feature_snapshot(
            symbol=self._symbol(symbol),
            candles=[
                row for row in self.candles(symbol, interval="1h", limit=100)
                if int(row[6]) <= int(now.timestamp() * 1000)
            ],
            book=self.order_book(symbol, limit=20),
            premium=self.premium(symbol),
            open_interest=self.open_interest(symbol),
            long_short=self.long_short_ratio(symbol),
            event_time=now,
            built_at=now,
            sentiment_score=sentiment_score,
        )


class StaleMarketData(RuntimeError):
    """A required cached component is absent or too old for safe decisions."""


class MultiCadenceMarketCache:
    """Refresh public market components independently and assemble one snapshot."""

    def __init__(
        self,
        client: BinanceUsdMClient,
        *,
        premium_interval_seconds: float = 5,
        book_interval_seconds: float = 15,
        derivatives_interval_seconds: float = 60,
    ):
        self.client = client
        self.intervals = {
            "premium": premium_interval_seconds,
            "book": book_interval_seconds,
            "open_interest": derivatives_interval_seconds,
            "long_short": derivatives_interval_seconds,
        }
        self._values: dict[str, object] = {}
        self._updated: dict[str, datetime] = {}
        self._candle_slot: datetime | None = None

    def _due(self, name: str, now: datetime) -> bool:
        updated = self._updated.get(name)
        return updated is None or (now - updated).total_seconds() >= self.intervals[name]

    def _refresh(self, name: str, now: datetime, fetch) -> None:
        if not self._due(name, now):
            return
        try:
            value = fetch()
        except Exception:
            return
        if value is not None:
            self._values[name] = value
            self._updated[name] = now

    def snapshot(
        self,
        symbol: str = "BTCUSDT",
        *,
        now: datetime | None = None,
        sentiment_score: float | None = 0.0,
    ) -> FeatureSnapshot:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("market cache time must be timezone-aware")
        now = now.astimezone(timezone.utc)
        candle_slot = now.replace(minute=0, second=0, microsecond=0)
        if self._candle_slot != candle_slot or "candles" not in self._values:
            try:
                rows = [
                    row
                    for row in self.client.candles(symbol, interval="1h", limit=100)
                    if int(row[6]) <= int(now.timestamp() * 1000)
                ]
            except Exception:
                rows = None
            if rows:
                self._values["candles"] = rows
                self._updated["candles"] = now
                self._candle_slot = candle_slot
        self._refresh("premium", now, lambda: self.client.premium(symbol))
        self._refresh("book", now, lambda: self.client.order_book(symbol, limit=20))
        self._refresh("open_interest", now, lambda: self.client.open_interest(symbol))
        self._refresh("long_short", now, lambda: self.client.long_short_ratio(symbol))

        maximum_age = {
            "premium": 10,
            "book": 30,
            "open_interest": 120,
            "long_short": 120,
            "candles": 7200,
        }
        stale = [
            name
            for name, age in maximum_age.items()
            if name not in self._values
            or name not in self._updated
            or (now - self._updated[name]).total_seconds() > age
        ]
        if stale:
            raise StaleMarketData("stale market components: " + ",".join(stale))
        return build_feature_snapshot(
            symbol=symbol,
            candles=self._values["candles"],
            book=self._values["book"],
            premium=self._values["premium"],
            open_interest=self._values["open_interest"],
            long_short=self._values["long_short"],
            event_time=now,
            built_at=now,
            sentiment_score=sentiment_score,
        )
