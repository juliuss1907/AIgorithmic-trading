"""Causal daily Donchian signal and bounded volatility sizing for BTC spot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from statistics import fmean
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from intraday.contracts import FeatureSnapshot, SpotRuleParameters
from intraday.market import compute_indicators


SPOT_PUBLIC_BASE_URL = "https://api.binance.com"
SPOT_ALLOWED_PATHS = {"/api/v3/klines", "/api/v3/depth"}


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


class BinanceSpotDailyClient:
    """Read-only public BTCUSDT daily candles; there is no order endpoint."""

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
        now: datetime | None = None,
    ) -> list[list]:
        if symbol != "BTCUSDT":
            raise ValueError("v1 only permits BTCUSDT")
        if not 35 <= limit <= 1000:
            raise ValueError("limit must be between 35 and 1000")
        now = now or datetime.now(timezone.utc)
        rows = self._fetch_json(
            "/api/v3/klines",
            {"symbol": symbol, "interval": "1d", "limit": limit},
        )
        cutoff = int(now.timestamp() * 1000)
        return [row for row in rows if int(row[6]) <= cutoff]

    def order_book(self, *, symbol: str = "BTCUSDT", limit: int = 20) -> dict:
        if symbol != "BTCUSDT":
            raise ValueError("v1 only permits BTCUSDT")
        if limit not in {5, 10, 20, 50, 100, 500, 1000}:
            raise ValueError("unsupported depth limit")
        return self._fetch_json(
            "/api/v3/depth", {"symbol": symbol, "limit": limit}
        )
