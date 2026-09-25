from datetime import datetime, timezone

import pytest

from intraday.contracts import SpotRuleParameters
from intraday.spot_signal import build_spot_feature_snapshot, evaluate_donchian


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
