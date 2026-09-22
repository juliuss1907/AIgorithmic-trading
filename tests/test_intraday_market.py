from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from intraday.market import BinanceUsdMClient, build_feature_snapshot, compute_indicators


def candles(count: int = 60):
    rows = []
    for index in range(count):
        close = 40_000 + index * 10
        rows.append(
            [
                1_700_000_000_000 + index * 60_000,
                str(close - 3),
                str(close + 5),
                str(close - 7),
                str(close),
                "100",
                1_700_000_059_999 + index * 60_000,
                "4000000",
                100,
                "60",
                "2400000",
                "0",
            ]
        )
    return rows


def test_indicator_calculation_is_finite_and_complete():
    result = compute_indicators(candles())

    assert set(result) == {
        "price",
        "volume_1h",
        "rsi14",
        "macd",
        "macd_signal",
        "macd_hist",
        "bb_mid",
        "bb_upper",
        "bb_lower",
        "buy_ratio",
        "path_efficiency",
    }
    assert 0 <= result["rsi14"] <= 100
    assert result["bb_lower"] < result["bb_mid"] < result["bb_upper"]
    assert result["buy_ratio"] == pytest.approx(0.6)
    assert result["path_efficiency"] == pytest.approx(1.0)


def test_snapshot_builder_produces_18_plus_features_and_quality_metadata():
    now = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)
    snapshot = build_feature_snapshot(
        symbol="BTCUSDT",
        candles=candles(),
        book={"bids": [["40589", "2"], ["40588", "1"]], "asks": [["40590", "1"]]},
        premium={"markPrice": "40589.5", "indexPrice": "40580", "lastFundingRate": "0.0001"},
        open_interest={"openInterest": "12345"},
        long_short={"longShortRatio": "1.2"},
        event_time=now,
        built_at=now,
        sentiment_score=0.25,
    )

    assert len(snapshot.features) >= 18
    assert snapshot.bid == 40589
    assert snapshot.ask == 40590
    assert snapshot.features["order_book_imbalance"] == pytest.approx(0.5)
    assert snapshot.features["basis_bps"] == pytest.approx((40589.5 / 40580 - 1) * 10_000)
    assert snapshot.freshness == {
        "candles": True,
        "order_book": True,
        "premium": True,
        "open_interest": True,
        "long_short_ratio": True,
        "sentiment": True,
    }
    assert snapshot.quality_flags == ()


def test_snapshot_marks_optional_missing_inputs_instead_of_fabricating_them():
    now = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)
    snapshot = build_feature_snapshot(
        symbol="BTCUSDT",
        candles=candles(),
        book={"bids": [["40589", "2"]], "asks": [["40590", "1"]]},
        premium={"markPrice": "40589.5", "indexPrice": "40580", "lastFundingRate": "0.0001"},
        open_interest=None,
        long_short=None,
        event_time=now,
        built_at=now,
        sentiment_score=None,
    )

    assert snapshot.features["open_interest"] is None
    assert snapshot.features["long_short_ratio"] is None
    assert snapshot.features["sentiment_score"] is None
    assert snapshot.freshness["open_interest"] is False
    assert "open_interest_missing" in snapshot.quality_flags


def test_binance_client_uses_only_allowlisted_public_futures_endpoints():
    calls = []

    def fetch(path, params):
        calls.append((path, params))
        return {"openInterest": "1"}

    client = BinanceUsdMClient(fetch_json=fetch)
    assert client.open_interest("BTCUSDT") == {"openInterest": "1"}
    assert calls == [("/fapi/v1/openInterest", {"symbol": "BTCUSDT"})]

    with pytest.raises(ValueError, match="BTCUSDT"):
        client.open_interest("ETHUSDT")


def test_snapshot_checksum_rejects_tampered_persisted_payload():
    now = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)
    original = build_feature_snapshot(
        symbol="BTCUSDT",
        candles=candles(),
        book={"bids": [["40589", "2"]], "asks": [["40590", "1"]]},
        premium={"markPrice": "40589.5", "indexPrice": "40580", "lastFundingRate": "0.0001"},
        open_interest={"openInterest": "123"},
        long_short={"longShortRatio": "1.2"},
        event_time=now,
        built_at=now,
        sentiment_score=0,
    )
    payload = original.model_dump()
    payload["features"]["price"] = 1

    with pytest.raises(ValidationError, match="checksum"):
        type(original).model_validate(payload)
