"""Causal daily Donchian signal and bounded volatility sizing for BTC spot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from statistics import fmean
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from intraday.assets import asset_spec
from intraday.contracts import FeatureSnapshot, SpotRuleParameters
from intraday.market import StaleMarketData, compute_indicators


SPOT_PUBLIC_BASE_URL = "https://api.binance.com"
SPOT_ALLOWED_PATHS = {"/api/v3/klines", "/api/v3/depth"}
SPOT_INTERVAL_MS = {"4h": 14_400_000, "8h": 28_800_000, "1d": 86_400_000}


@dataclass(frozen=True)
class DonchianObservation:
    close: float
    entry_channel: float
    exit_channel: float
    atr: float
    atr_pct: float
    size_multiplier: float
    entry: bool
    exit: bool


def evaluate_donchian(
    candles: list[list], rule: SpotRuleParameters
) -> DonchianObservation:
    required = max(rule.entry_window, rule.exit_window, rule.atr_period) + 1
    if len(candles) < required:
        raise ValueError(f"Donchian signal requires at least {required} closed candles")
    highs = [float(row[2]) for row in candles]
    lows = [float(row[3]) for row in candles]
    closes = [float(row[4]) for row in candles]
    close = closes[-1]
    entry_channel = max(highs[-rule.entry_window - 1:-1])
    exit_channel = min(lows[-rule.exit_window - 1:-1])
    true_ranges = []
    for index in range(len(candles) - rule.atr_period, len(candles)):
        previous_close = closes[index - 1]
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - previous_close),
                abs(lows[index] - previous_close),
            )
        )
    atr = fmean(true_ranges)
    atr_pct = atr / close if close > 0 else 0
    size_multiplier = min(1.0, 0.02 / atr_pct) if atr_pct > 0 else 0.0
    return DonchianObservation(
        close=close,
        entry_channel=entry_channel,
        exit_channel=exit_channel,
        atr=atr,
        atr_pct=atr_pct,
        size_multiplier=size_multiplier,
        entry=close > entry_channel,
        exit=close < exit_channel,
    )


def build_spot_feature_snapshot(
    *,
    symbol: str,
    candles: list[list],
    book: dict,
    event_time: datetime,
    built_at: datetime,
    sentiment_score: float | None,
) -> FeatureSnapshot:
    """Build scope-correct spot evidence from closed daily candles and live quotes."""
    indicators = compute_indicators(candles)
    volume_1d = indicators.pop("volume_1h")
    bids = [(float(price), float(quantity)) for price, quantity, *_ in book["bids"][:20]]
    asks = [(float(price), float(quantity)) for price, quantity, *_ in book["asks"][:20]]
    if not bids or not asks:
        raise ValueError("spot order book must contain both bids and asks")
    bid, ask = bids[0][0], asks[0][0]
    midpoint = (bid + ask) / 2
    bid_quantity = sum(quantity for _, quantity in bids)
    ask_quantity = sum(quantity for _, quantity in asks)
    total_quantity = bid_quantity + ask_quantity
    candle_close = float(indicators["price"])
    candle_closed_at = datetime.fromtimestamp(
        int(candles[-1][6]) / 1000, tz=timezone.utc
    )
    features = {
        **indicators,
        "volume_1d": volume_1d,
        "reference_price": midpoint,
        "candle_close_price": candle_close,
        "reference_to_close_bps": (midpoint / candle_close - 1) * 10_000,
        "reference_mid_dislocation_bps": 0.0,
        "closed_candle_age_seconds": max(
            0.0, (event_time - candle_closed_at).total_seconds()
        ),
        "order_book_imbalance": (
            (bid_quantity - ask_quantity) / total_quantity if total_quantity else 0.0
        ),
        "spread_bps": (ask - bid) / midpoint * 10_000,
        "sentiment_score": sentiment_score,
    }
    freshness = {
        "candles": True,
        "order_book": True,
        "sentiment": sentiment_score is not None,
    }
    flags = tuple(f"{name}_missing" for name, fresh in freshness.items() if not fresh)
    return FeatureSnapshot.create(
        symbol=symbol,
        market="binance_spot",
        timeframe="1d",
        feature_schema_version="2",
        event_time=event_time,
        built_at=built_at,
        bid=bid,
        ask=ask,
        features=features,
        freshness=freshness,
        quality_flags=flags,
    )


def build_spot_multiframe_snapshot(
    *, symbol: str, candle_sets: dict[str, list[list]], book: dict,
    event_time: datetime, built_at: datetime, sentiment_score: float | None,
) -> FeatureSnapshot:
    """Causal 4h trigger with Binance-native 8h/1d context, never an entry signal."""
    now_ms = int(event_time.timestamp() * 1000)
    for interval, length_ms in SPOT_INTERVAL_MS.items():
        rows = candle_sets.get(interval, [])
        if len(rows) < 35 or int(rows[-1][6]) >= now_ms:
            raise StaleMarketData(f"candles_{interval} missing or unclosed")
        age_ms = now_ms - int(rows[-1][6])
        if age_ms > length_ms + 300_000:
            raise StaleMarketData(f"candles_{interval} stale")
        if any(int(row[6]) != int(row[0]) + length_ms - 1 for row in rows):
            raise StaleMarketData(f"candles_{interval} invalid interval")
    primary = build_spot_feature_snapshot(
        symbol=symbol, candles=candle_sets["4h"], book=book,
        event_time=event_time, built_at=built_at, sentiment_score=sentiment_score,
    )
    features = dict(primary.features)
    features["volume_4h"] = features.pop("volume_1d")
    for interval in ("8h", "1d"):
        context = compute_indicators(candle_sets[interval])
        for name in ("price", "rsi14", "macd", "macd_hist", "path_efficiency"):
            features[f"context_{interval}_{name}"] = context[name]
        features[f"context_{interval}_volume"] = context["volume_1h"]
    return FeatureSnapshot.create(
        symbol=symbol, market="binance_spot", timeframe="4h",
        feature_schema_version="3", event_time=event_time, built_at=built_at,
        bid=primary.bid, ask=primary.ask, features=features,
        freshness={**primary.freshness, **{
            f"candles_{interval}": True for interval in SPOT_INTERVAL_MS
        }},
        quality_flags=primary.quality_flags,
    )
class BinanceSpotDailyClient:
    """Read-only public daily candles and quotes for registered spot assets."""

    def __init__(
        self,
        *,
        fetch_json: Callable[[str, dict], object] | None = None,
        timeout_seconds: float = 10,
    ):
        self._fetch_json = fetch_json or self._http_get
        self.timeout_seconds = timeout_seconds

    def _http_get(self, path: str, params: dict):
        if path not in SPOT_ALLOWED_PATHS:
            raise ValueError("endpoint is not allowlisted")
        request = Request(
            f"{SPOT_PUBLIC_BASE_URL}{path}?{urlencode(params)}",
            headers={"User-Agent": "system-trading-lab/0.1"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.load(response)

    def candles(
        self,
        *,
        symbol: str = "BTCUSDT",
        limit: int = 100,
        interval: str = "1d",
        now: datetime | None = None,
    ) -> list[list]:
        symbol = asset_spec(symbol).binance_spot_symbol
        if not 35 <= limit <= 1000:
            raise ValueError("limit must be between 35 and 1000")
        if interval not in SPOT_INTERVAL_MS:
            raise ValueError("unsupported spot candle interval")
        now = now or datetime.now(timezone.utc)
        rows = self._fetch_json(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        cutoff = int(now.timestamp() * 1000)
        return [row for row in rows if int(row[6]) <= cutoff]

    def backfill(
        self, *, symbol: str, interval: str, start_time: int,
        end_time: int, now: datetime | None = None,
    ) -> list[list]:
        """Page forward through public klines, retaining only closed UTC bars."""
        symbol = asset_spec(symbol).binance_spot_symbol
        if interval not in SPOT_INTERVAL_MS:
            raise ValueError("unsupported spot candle interval")
        length_ms = SPOT_INTERVAL_MS[interval]
        if start_time < 0 or end_time < start_time or start_time % length_ms:
            raise ValueError("invalid UTC backfill window")
        now = now or datetime.now(timezone.utc)
        cutoff = min(end_time, int(now.timestamp() * 1000) - 1)
        result: list[list] = []
        cursor = start_time
        while cursor <= cutoff:
            limit = min(1000, (cutoff - cursor) // length_ms + 1)
            rows = self._fetch_json(
                "/api/v3/klines", {"symbol": symbol, "interval": interval,
                                    "startTime": cursor, "endTime": cutoff,
                                    "limit": limit},
            )
            if not isinstance(rows, list) or not rows:
                break
            previous = cursor - length_ms
            for row in rows:
                opening = int(row[0])
                if opening < cursor or opening <= previous or opening % length_ms:
                    raise ValueError("non-monotonic Binance candle page")
                if int(row[6]) != opening + length_ms - 1:
                    raise ValueError("invalid Binance candle close time")
                if int(row[6]) < int(now.timestamp() * 1000) and opening <= cutoff:
                    result.append(row)
                previous = opening
            cursor = previous + length_ms
        return result

    def order_book(self, *, symbol: str = "BTCUSDT", limit: int = 20) -> dict:
        symbol = asset_spec(symbol).binance_spot_symbol
        if limit not in {5, 10, 20, 50, 100, 500, 1000}:
            raise ValueError("unsupported depth limit")
        return self._fetch_json(
            "/api/v3/depth", {"symbol": symbol, "limit": limit}
        )


class MultiCadenceSpotCache:
    """Cache closed daily candles separately from live Binance Spot quotes."""

    def __init__(
        self,
        client: BinanceSpotDailyClient,
        *,
        quote_interval_seconds: float = 15,
    ):
        self.client = client
        self.quote_interval_seconds = quote_interval_seconds
        self._values: dict[str, object] = {}
        self._updated: dict[str, datetime] = {}
        self._candle_day = None

    def closed_candles(self) -> list[list]:
        return list(self._values.get("candles", []))

    def snapshot(
        self,
        symbol: str = "BTCUSDT",
        *,
        now: datetime | None = None,
        sentiment_score: float | None = 0.0,
        candle_limit: int = 100,
    ) -> FeatureSnapshot:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("spot cache time must be timezone-aware")
        now = now.astimezone(timezone.utc)
        if self._candle_day != now.date() or "candles" not in self._values:
            try:
                candles = self.client.candles(
                    symbol=symbol, limit=candle_limit, now=now
                )
            except Exception:
                candles = None
            if candles:
                self._values["candles"] = candles
                self._updated["candles"] = now
                self._candle_day = now.date()
        quote_updated = self._updated.get("order_book")
        if (
            quote_updated is None
            or (now - quote_updated).total_seconds() >= self.quote_interval_seconds
        ):
            try:
                book = self.client.order_book(symbol=symbol, limit=20)
            except Exception:
                book = None
            if book is not None:
                self._values["order_book"] = book
                self._updated["order_book"] = now

        maximum_age = {"candles": 26 * 3600, "order_book": 30}
        stale = [
            name
            for name, age in maximum_age.items()
            if name not in self._values
            or name not in self._updated
            or (now - self._updated[name]).total_seconds() > age
        ]
        if stale:
            raise StaleMarketData(
                "stale spot market components: " + ",".join(stale)
            )
        return build_spot_feature_snapshot(
            symbol=symbol,
            candles=self._values["candles"],
            book=self._values["order_book"],
            event_time=now,
            built_at=now,
            sentiment_score=sentiment_score,
        )


class MultiTimeframeSpotCache:
    """Per-symbol native UTC 4h/8h/1d bars plus independently refreshed quotes."""

    def __init__(
        self, client: BinanceSpotDailyClient, *, quote_interval_seconds: float = 15,
    ):
        self.client = client
        self.quote_interval_seconds = quote_interval_seconds
        self._symbol: str | None = None
        self._candles: dict[str, list[list]] = {}
        self._slots: dict[str, int] = {}
        self._book: dict | None = None
        self._book_at: datetime | None = None

    def closed_candles(self, interval: str = "4h") -> list[list]:
        if interval not in SPOT_INTERVAL_MS:
            raise ValueError("unsupported spot candle interval")
        return list(self._candles.get(interval, []))

    def snapshot(
        self, symbol: str = "BTCUSDT", *, now: datetime | None = None,
        sentiment_score: float | None = 0.0, candle_limit: int = 100,
    ) -> FeatureSnapshot:
        symbol = asset_spec(symbol).symbol
        if self._symbol is not None and self._symbol != symbol:
            raise ValueError("spot cache cannot mix asset symbols")
        self._symbol = symbol
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("spot cache time must be timezone-aware")
        now = now.astimezone(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        for interval, width in SPOT_INTERVAL_MS.items():
            slot = now_ms // width
            if self._slots.get(interval) == slot and interval in self._candles:
                continue
            try:
                rows = self.client.candles(
                    symbol=symbol, interval=interval, limit=candle_limit, now=now,
                )
            except Exception:
                continue
            if (rows and int(rows[-1][6]) < now_ms
                    and now_ms - int(rows[-1][6]) <= width + 300_000):
                self._candles[interval] = rows
                self._slots[interval] = slot
        if (self._book_at is None or
                (now - self._book_at).total_seconds() >= self.quote_interval_seconds):
            try:
                book = self.client.order_book(symbol=symbol, limit=20)
            except Exception:
                book = None
            if book is not None:
                self._book = book
                self._book_at = now
        if (self._book is None or self._book_at is None or
                (now - self._book_at).total_seconds() > 30):
            raise StaleMarketData("stale spot market components: order_book")
        return build_spot_multiframe_snapshot(
            symbol=symbol, candle_sets=self._candles, book=self._book,
            event_time=now, built_at=now, sentiment_score=sentiment_score,
        )
