from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import SpotRuleParameters
from intraday.market import StaleMarketData
from intraday.spot_signal import (
    BinanceSpotDailyClient,
    MultiCadenceSpotCache,
    build_spot_feature_snapshot,
    evaluate_donchian,
)


def rows(closes):
    result = []
    for index, close in enumerate(closes):
        result.append(
            [
                index * 86_400_000,
                str(close - 1),
                str(close + 2),
                str(close - 2),
                str(close),
                "10",
                (index + 1) * 86_400_000 - 1,
                "0", "0", "5",
            ]
        )
    return result


def test_donchian_breakout_and_atr_volatility_sizing_are_causal():
    history = [100 + index * 0.1 for index in range(31)]
    history[-1] = 110

    signal = evaluate_donchian(
        rows(history),
        SpotRuleParameters(entry_window=20, exit_window=10, atr_period=14),
    )

    assert signal.entry is True
    assert signal.exit is False
    assert 0 < signal.size_multiplier <= 1
    assert signal.entry_channel < signal.close


def test_donchian_exit_is_independent_from_ai_decision():
    history = [100 + index * 0.1 for index in range(30)] + [80]

    signal = evaluate_donchian(
        rows(history),
        SpotRuleParameters(entry_window=20, exit_window=10, atr_period=14),
    )

    assert signal.entry is False
    assert signal.exit is True
    assert signal.close < signal.exit_channel


def test_spot_snapshot_uses_book_midpoint_and_closed_daily_candle():
    candles = rows([100 + index for index in range(40)])
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)

    snapshot = build_spot_feature_snapshot(
        symbol="BTCUSDT",
        candles=candles,
        book={
            "bids": [["140.0", "2"], ["139.9", "1"]],
            "asks": [["140.2", "1"], ["140.3", "2"]],
        },
        event_time=now,
        built_at=now,
        sentiment_score=0.1,
    )

    assert snapshot.market == "binance_spot"
    assert snapshot.timeframe == "1d"
    assert snapshot.feature_schema_version == "2"
    assert snapshot.features["price"] == 139
    assert snapshot.features["candle_close_price"] == 139
    assert snapshot.features["reference_price"] == pytest.approx(140.1)
    assert snapshot.bid == 140
    assert snapshot.ask == 140.2
    assert snapshot.features["volume_1d"] == 10


def test_spot_client_accepts_registered_assets_only():
    calls = []

    def fetch(path, params):
        calls.append((path, params))
        return {"bids": [], "asks": []}

    client = BinanceSpotDailyClient(fetch_json=fetch)

    assert client.order_book(symbol="ethusdt") == {"bids": [], "asks": []}
    assert calls == [("/api/v3/depth", {"symbol": "ETHUSDT", "limit": 20})]

    with pytest.raises(ValueError, match="unsupported asset symbol"):
        client.order_book(symbol="DOGEUSDT")


def test_spot_cache_refreshes_quotes_without_refetching_daily_candles():
    class Client:
        def __init__(self):
            self.candle_calls = 0
            self.book_calls = 0

        def candles(self, **kwargs):
            self.candle_calls += 1
            return rows([100 + index for index in range(40)])

        def order_book(self, **kwargs):
            self.book_calls += 1
            return {"bids": [["140", "2"]], "asks": [["140.2", "1"]]}

    client = Client()
    cache = MultiCadenceSpotCache(client, quote_interval_seconds=15)
    now = datetime(2026, 9, 25, 1, tzinfo=timezone.utc)

    for seconds in (0, 5, 15):
        snapshot = cache.snapshot(now=now + timedelta(seconds=seconds))

    assert snapshot.features["reference_price"] == pytest.approx(140.1)
    assert client.candle_calls == 1
    assert client.book_calls == 2


def test_spot_cache_fails_closed_when_quote_is_unavailable():
    class Client:
        def candles(self, **kwargs):
            return rows([100 + index for index in range(40)])

        def order_book(self, **kwargs):
            raise TimeoutError

    cache = MultiCadenceSpotCache(Client())

    with pytest.raises(StaleMarketData, match="order_book"):
        cache.snapshot(now=datetime(2026, 9, 25, 1, tzinfo=timezone.utc))


def test_spot_client_backfills_native_4h_candles_in_bounded_pages():
    calls = []
    interval_ms = 4 * 3_600_000
    start = 1_700_006_400_000
    start -= start % interval_ms
    now = datetime.fromtimestamp((start + 1002.5 * interval_ms) / 1000, timezone.utc)

    def fetch(path, params):
        calls.append(params.copy())
        opening = int(params["startTime"])
        count = min(int(params["limit"]), 1003 - (opening - start) // interval_ms)
        return [
            [opening + index * interval_ms, "100", "102", "99", "101", "10",
             opening + (index + 1) * interval_ms - 1, "0", "0", "5"]
            for index in range(max(0, count))
        ]

    client = BinanceSpotDailyClient(fetch_json=fetch)
    candles = client.backfill(symbol="ETHUSDT", interval="4h", start_time=start,
                              end_time=start + 1002 * interval_ms, now=now)

    assert len(candles) == 1002
    assert [call["limit"] for call in calls] == [1000, 3]
    assert all(call["interval"] == "4h" for call in calls)
    assert candles[-1][6] < int(now.timestamp() * 1000)


def test_spot_multiframe_snapshot_requires_closed_fresh_native_context():
    from intraday.spot_signal import build_spot_multiframe_snapshot

    now = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)

    def candles(interval_hours):
        interval_ms = interval_hours * 3_600_000
        last_open = int(now.timestamp() * 1000) // interval_ms * interval_ms - interval_ms
        return [
            [last_open - (39 - index) * interval_ms, "100", "102", "99", "101",
             "10", last_open - (38 - index) * interval_ms - 1, "0", "0", "5"]
            for index in range(40)
        ]

    bundle = {"4h": candles(4), "8h": candles(8), "1d": candles(24)}
    book = {"bids": [["100", "2"]], "asks": [["100.2", "1"]]}
    snapshot = build_spot_multiframe_snapshot(
        symbol="ETHUSDT", candle_sets=bundle, book=book,
        event_time=now, built_at=now, sentiment_score=0.1,
    )
    assert snapshot.timeframe == "4h"
    assert snapshot.feature_schema_version == "3"
    assert snapshot.features["context_8h_price"] == 101
    assert snapshot.features["context_1d_price"] == 101
    assert snapshot.freshness["candles_8h"] is True

    stale = dict(bundle)
    stale["8h"] = candles(8)[:-1]
    with pytest.raises(StaleMarketData, match="candles_8h"):
        build_spot_multiframe_snapshot(
            symbol="ETHUSDT", candle_sets=stale, book=book,
            event_time=now, built_at=now, sentiment_score=0.1,
        )


def test_multiframe_cache_fetches_three_native_intervals_and_reuses_closed_bars():
    from intraday.spot_signal import MultiTimeframeSpotCache

    now = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
    calls = []

    class Client:
        def candles(self, *, symbol, interval, limit, now):
            calls.append(interval)
            width = {"4h": 14_400_000, "8h": 28_800_000,
                     "1d": 86_400_000}[interval]
            last_open = int(now.timestamp() * 1000) // width * width - width
            return [[last_open - (39 - index) * width, "100", "102", "99",
                     "101", "10", last_open - (38 - index) * width - 1,
                     "0", "0", "5"] for index in range(40)]

        def order_book(self, *, symbol, limit):
            return {"bids": [["100", "2"]], "asks": [["100.2", "1"]]}

    cache = MultiTimeframeSpotCache(Client())
    first = cache.snapshot("ETHUSDT", now=now)
    second = cache.snapshot("ETHUSDT", now=now + timedelta(seconds=15))
    assert first.timeframe == second.timeframe == "4h"
    assert calls == ["4h", "8h", "1d"]
    assert len(cache.closed_candles("4h")) == 40
