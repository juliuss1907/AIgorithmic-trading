"""Regression proof for normalized content-addressed external market evidence."""

from datetime import datetime, timezone

import pytest

from intraday.assets import ASSET_REGISTRY
from intraday.contracts import ExternalObservation, VenueMarketFrame
from intraday.cross_venue import build_hyperliquid_frame
from intraday.store import IntradayStore


NOW = datetime(2026, 10, 4, 9, tzinfo=timezone.utc)
SYMBOLS = tuple(ASSET_REGISTRY)
SOURCES = ("cryptorank", "aster", "variational", "lighter")


def frame_values(symbol="BTCUSDT", venue="hyperliquid"):
    return {
        "venue": venue, "symbol": symbol,
        "event_time": NOW, "received_at": NOW, "metadata_received_at": NOW,
        "bid": 99.0, "ask": 101.0, "mark_price": 100.0, "index_price": 100.0,
        "funding_bps_hour": 0.0, "open_interest_usd": 1000.0,
        "spread_bps": 200.0,
        "bid_depth_usd": {"5": 0.0, "10": 0.0, "25": 100.0},
        "ask_depth_usd": {"5": 0.0, "10": 0.0, "25": 200.0},
        "book_imbalance": {"5": 0.0, "10": 0.0, "25": -1.0 / 3},
    }


def observation_values(source="aster", symbol="BTCUSDT"):
    return {
        "source": source,
        "dataset": "market_context" if source == "cryptorank" else "perp_market",
        "symbol": symbol, "source_timestamp": NOW, "received_at": NOW,
        "metrics": {"mark_price": 100.0, "open_interest_usd": 0.0, "missing": None},
        "labels": {"status": "shadow"},
    }


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("venue", ("hyperliquid", "lighter"))
def test_frame_integer_and_float_payloads_have_same_valid_identity(symbol, venue):
    floats = frame_values(symbol, venue)
    integers = {
        key: int(value) if isinstance(value, float) else value
        for key, value in floats.items()
    }
    for key in ("bid_depth_usd", "ask_depth_usd"):
        integers[key] = {name: int(value) for name, value in floats[key].items()}
    integers["book_imbalance"] = {"5": 0, "10": 0, "25": -1.0 / 3}

    normalized = VenueMarketFrame.create(**integers)
    reference = VenueMarketFrame.create(**floats)

    assert normalized == reference
    assert normalized.frame_id == reference.frame_id
    assert normalized.checksum == reference.checksum


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("half_spread_bps", (1.0, 6.0, 12.0, 30.0))
def test_hyperliquid_empty_depth_buckets_do_not_break_any_coin(symbol, half_spread_bps):
    spec = ASSET_REGISTRY[symbol]
    mid = 100.0
    book = {
        "coin": spec.hyperliquid_coin, "time": int(NOW.timestamp() * 1000),
        "levels": [
            [{"px": str(mid * (1 - half_spread_bps / 10_000)), "sz": "1.0"}],
            [{"px": str(mid * (1 + half_spread_bps / 10_000)), "sz": "1.0"}],
        ],
    }
    context = {"markPx": "100", "oraclePx": "100", "funding": "0", "openInterest": "10"}

    frame = build_hyperliquid_frame(book, context, symbol=symbol, received_at=NOW)

    assert frame.symbol == symbol
    assert VenueMarketFrame.model_validate_json(frame.model_dump_json()) == frame
    for bucket in (5, 10, 25):
        if half_spread_bps > bucket:
            assert frame.bid_depth_usd[str(bucket)] == 0.0
            assert frame.ask_depth_usd[str(bucket)] == 0.0
            assert frame.book_imbalance[str(bucket)] == 0.0


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("source", SOURCES)
def test_observation_integer_metrics_are_normalized_before_hash(source, symbol):
    floats = observation_values(source, symbol)
    integers = {**floats, "metrics": {"mark_price": 100, "open_interest_usd": 0, "missing": None}}

    normalized = ExternalObservation.create(**integers)
    reference = ExternalObservation.create(**floats)

    assert normalized == reference
    assert normalized.observation_id == reference.observation_id


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_normalized_frame_json_and_sqlite_round_trip_is_idempotent(symbol, tmp_path):
    values = frame_values(symbol)
    values["bid_depth_usd"] = {"5": 0, "10": 0, "25": 100}
    frame = VenueMarketFrame.create(**values)
    restored = VenueMarketFrame.model_validate_json(frame.model_dump_json())
    store = IntradayStore(tmp_path / "roundtrip.sqlite")

    store.record_venue_frame(frame)
    store.record_venue_frame(restored)

    assert store.list_venue_frames("hyperliquid", symbol=symbol) == [frame]


@pytest.mark.parametrize("source", SOURCES)
def test_normalized_observation_json_and_sqlite_round_trip(source, tmp_path):
    values = observation_values(source, None if source == "cryptorank" else "BTCUSDT")
    values["metrics"] = {"value": 0, "unavailable": None}
    observation = ExternalObservation.create(**values)
    restored = ExternalObservation.model_validate_json(observation.model_dump_json())
    store = IntradayStore(tmp_path / "roundtrip.sqlite")

    store.record_external_observation(observation)
    store.record_external_observation(restored)

    assert store.list_external_observations(source=source) == [observation]


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_real_frame_payload_mutations_still_fail_checksum(symbol):
    frame = VenueMarketFrame.create(**frame_values(symbol))
    payload = frame.model_dump()
    payload["bid_depth_usd"] = {**payload["bid_depth_usd"], "5": 1.0}

    with pytest.raises(ValueError, match="checksum"):
        VenueMarketFrame.model_validate(payload)


@pytest.mark.parametrize("source", SOURCES)
def test_real_observation_payload_mutations_still_fail_checksum(source):
    observation = ExternalObservation.create(**observation_values(source))
    payload = observation.model_dump()
    payload["metrics"] = {**payload["metrics"], "mark_price": 101.0}

    with pytest.raises(ValueError, match="checksum"):
        ExternalObservation.model_validate(payload)


@pytest.mark.parametrize("map_name", ("bid_depth_usd", "ask_depth_usd", "book_imbalance"))
@pytest.mark.parametrize("bad_number", (float("nan"), float("inf"), float("-inf")))
def test_frame_rejects_nonfinite_map_values(map_name, bad_number):
    values = frame_values()
    values[map_name] = {**values[map_name], "5": bad_number}

    with pytest.raises(ValueError):
        VenueMarketFrame.create(**values)


@pytest.mark.parametrize("bad_number", (float("nan"), float("inf"), float("-inf")))
def test_observation_rejects_nonfinite_metrics(bad_number):
    values = observation_values()
    values["metrics"] = {"value": bad_number}

    with pytest.raises(ValueError, match="finite"):
        ExternalObservation.create(**values)


@pytest.mark.parametrize("update", (
    {"bid_depth_usd": {"5": -1.0, "10": 0.0, "25": 100.0}},
    {"ask_depth_usd": {"5": 0.0}},
    {"book_imbalance": {"5": 2.0, "10": 0.0, "25": 0.0}},
    {"bid": 102.0}, {"symbol": "BTC/USDT"},
    {"received_at": datetime(2026, 10, 4, 9)}, {"unexpected": "field"},
))
def test_factory_rejects_malformed_frame_before_publication(update):
    with pytest.raises(ValueError):
        VenueMarketFrame.create(**{**frame_values(), **update})


@pytest.mark.parametrize("update", (
    {"metrics": {}}, {"metrics": {"": 1.0}}, {"metrics": {"bad": "invalid-number"}},
    {"source": "unknown"}, {"symbol": "BTC/USDT"},
    {"received_at": datetime(2026, 10, 4, 9)}, {"unexpected": "field"},
))
def test_factory_rejects_malformed_observation_before_publication(update):
    with pytest.raises(ValueError):
        ExternalObservation.create(**{**observation_values(), **update})


def test_valid_float_frame_keeps_exact_pre_hotfix_hash_and_json_identity():
    # Frozen on b9ec479: no hash/schema migration is allowed for existing valid evidence.
    checksum = "3125c43f908cdaab0cdd27545a241cfd02f1b489bc7cd9feca4c621d97128f0c"
    old_payload = {"schema_version": "1", **frame_values(), "frame_id": checksum[:24], "checksum": checksum}

    legacy = VenueMarketFrame.model_validate(old_payload)
    fresh = VenueMarketFrame.create(**frame_values())

    assert fresh == legacy
    assert fresh.checksum == checksum
    assert VenueMarketFrame.model_validate_json(legacy.model_dump_json()) == legacy


def test_valid_float_observation_keeps_exact_pre_hotfix_hash_and_json_identity():
    checksum = "a46f1f94b5910db622bdda18c3b89b517fa8af34db8b7c1dc5754413a55d1fc5"
    values = {
        "source": "cryptorank", "dataset": "market_context", "symbol": None,
        "source_timestamp": NOW, "received_at": NOW,
        "metrics": {"fear_greed_value": 42.0, "altcoin_index_value": None},
        "labels": {"fear_greed_classification": "Fear"},
    }
    legacy = ExternalObservation.model_validate({
        "schema_version": "1", **values,
        "observation_id": checksum[:24], "checksum": checksum,
    })

    assert ExternalObservation.create(**values) == legacy
    assert ExternalObservation.model_validate_json(legacy.model_dump_json()) == legacy
