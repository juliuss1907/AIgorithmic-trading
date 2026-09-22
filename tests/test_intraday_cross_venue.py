from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import Direction, JevDecision, Regime, RiskLevel, VenueMarketFrame
from intraday.cross_venue import (
    CrossVenuePolicy,
    CrossVenueThresholds,
    build_hyperliquid_frame,
    derive_cross_venue_thresholds,
    enrich_with_cross_venue,
)
from intraday.market import build_feature_snapshot
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 22, 12, 0, 5, tzinfo=timezone.utc)


def hyperliquid_book():
    return {
        "coin": "BTC",
        "time": int(NOW.timestamp() * 1000),
        "levels": [
            [
                {"px": "99990", "sz": "1.0", "n": 1},
                {"px": "99950", "sz": "2.0", "n": 2},
                {"px": "99800", "sz": "3.0", "n": 3},
            ],
            [
                {"px": "100010", "sz": "0.5", "n": 1},
                {"px": "100050", "sz": "1.0", "n": 2},
                {"px": "100200", "sz": "4.0", "n": 3},
            ],
        ],
    }


def asset_context():
    return {
        "funding": "0.0000125",
        "openInterest": "1000",
        "oraclePx": "100000",
        "markPx": "100005",
        "midPx": "100000",
    }


def test_hyperliquid_frame_normalizes_funding_oi_and_bps_depth():
    frame = build_hyperliquid_frame(
        hyperliquid_book(), asset_context(), received_at=NOW
    )

    assert frame.venue == "hyperliquid"
    assert frame.symbol == "BTCUSDT"
    assert frame.funding_bps_hour == pytest.approx(0.125)
    assert frame.open_interest_usd == pytest.approx(100_005_000)
    assert frame.spread_bps == pytest.approx(2.0)
    assert frame.bid_depth_usd["5"] == pytest.approx(299_890)
    assert frame.ask_depth_usd["5"] == pytest.approx(150_055)
    assert frame.book_imbalance["5"] == pytest.approx(
        (299_890 - 150_055) / (299_890 + 150_055)
    )
    assert frame.bid_depth_usd["25"] == pytest.approx(599_290)
    assert frame.ask_depth_usd["25"] == pytest.approx(550_855)


def test_frame_checksum_rejects_payload_mutation():
    frame = build_hyperliquid_frame(
        hyperliquid_book(), asset_context(), received_at=NOW
    )

    with pytest.raises(ValueError, match="checksum"):
        VenueMarketFrame.model_validate(
            {**frame.model_dump(), "mark_price": frame.mark_price + 1}
        )


def test_hyperliquid_frame_rejects_crossed_or_empty_books():
    empty = {**hyperliquid_book(), "levels": [[], []]}
    crossed = hyperliquid_book()
    crossed["levels"][0][0]["px"] = "100020"

    with pytest.raises(ValueError, match="both sides"):
        build_hyperliquid_frame(empty, asset_context(), received_at=NOW)
    with pytest.raises(ValueError, match="crossed"):
        build_hyperliquid_frame(crossed, asset_context(), received_at=NOW)


def binance_snapshot():
    candles = []
    for index in range(60):
        close = 99_400 + index * 10
        candles.append([
            1_700_000_000_000 + index * 60_000,
            str(close), str(close), str(close), str(close), "100",
            1_700_000_059_999 + index * 60_000,
            "0", 10, "60", "0", "0",
        ])
    return build_feature_snapshot(
        symbol="BTCUSDT",
        candles=candles,
        book={"bids": [["99990", "1"]], "asks": [["100010", "1"]]},
        premium={
            "markPrice": "100000", "indexPrice": "99995", "lastFundingRate": "0.00008"
        },
        open_interest={"openInterest": "5000"},
        long_short={"longShortRatio": "1.1"},
        event_time=NOW,
        built_at=NOW,
        sentiment_score=0,
    )


def test_cross_venue_features_are_bound_into_the_snapshot_checksum():
    frame = build_hyperliquid_frame(
        hyperliquid_book(), asset_context(), received_at=NOW
    )

    enriched = enrich_with_cross_venue(binance_snapshot(), frame, history=[])

    assert enriched.snapshot_id != binance_snapshot().snapshot_id
    assert enriched.features["hl_mark_price"] == pytest.approx(100_005)
    assert enriched.features["xv_mark_dislocation_bps"] == pytest.approx(0.5)
    assert enriched.features["xv_funding_spread_bps_hour"] == pytest.approx(0.025)
    assert enriched.features["xv_coverage_score"] == 1
    assert enriched.quality_flags == ()
    assert enriched.freshness == binance_snapshot().freshness


def test_missing_or_stale_hyperliquid_is_neutral_and_does_not_poison_core_freshness():
    snapshot = binance_snapshot()
    stale = build_hyperliquid_frame(
        hyperliquid_book(), asset_context(), received_at=NOW
    ).model_copy(update={"received_at": NOW.replace(second=0)})

    missing = enrich_with_cross_venue(snapshot, None, history=[])
    expired = enrich_with_cross_venue(snapshot, stale, history=[])

    for result in (missing, expired):
        assert result.features["hl_mark_price"] is None
        assert result.features["xv_coverage_score"] == 0
        assert result.freshness == snapshot.freshness
        assert result.quality_flags == snapshot.quality_flags


def test_venue_frames_are_stored_idempotently_and_listed_chronologically(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    frame = build_hyperliquid_frame(
        hyperliquid_book(), asset_context(), received_at=NOW
    )

    store.record_venue_frame(frame)
    store.record_venue_frame(frame)

    assert store.venue_frame_count("hyperliquid") == 1
    assert store.list_venue_frames("hyperliquid", limit=10) == [frame]


def test_venue_frame_retention_removes_only_expired_rows(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    old_time = NOW - timedelta(days=31)
    old = build_hyperliquid_frame(
        {**hyperliquid_book(), "time": int(old_time.timestamp() * 1000)},
        asset_context(),
        received_at=old_time,
    )
    current = build_hyperliquid_frame(
        hyperliquid_book(), asset_context(), received_at=NOW
    )
    store.record_venue_frame(old)
    store.record_venue_frame(current)

    removed = store.prune_venue_frames(before=NOW - timedelta(days=30))

    assert removed == 1
    assert store.list_venue_frames("hyperliquid") == [current]


def jev_decision(snapshot, direction=Direction.BUY, entry_quality=4):
    return JevDecision(
        decision_id="decision-cross-venue",
        tick_id="tick-cross-venue",
        snapshot_id=snapshot.snapshot_id,
        direction=direction,
        direction_confidence=0.91,
        regime=Regime.TRENDING_UP,
        toxic_flow=0.1,
        entry_quality=entry_quality,
        risk_level=RiskLevel.LOW,
        model_ref="stub/jev-v1",
        created_at=NOW,
    )


def with_features(snapshot, **updates):
    return type(snapshot).create(
        symbol=snapshot.symbol,
        event_time=snapshot.event_time,
        built_at=snapshot.built_at,
        bid=snapshot.bid,
        ask=snapshot.ask,
        features={**snapshot.features, **updates},
        freshness=snapshot.freshness,
        quality_flags=snapshot.quality_flags,
    )


def test_overlay_classifies_confirming_conflicting_stressed_and_unavailable():
    base = enrich_with_cross_venue(
        binance_snapshot(),
        build_hyperliquid_frame(hyperliquid_book(), asset_context(), received_at=NOW),
        history=[],
    )
    confirming = with_features(base, hl_return_30s_pct=0.02, hl_book_imbalance_10bps=0.2)
    conflicting = with_features(base, hl_return_30s_pct=-0.02, hl_book_imbalance_10bps=-0.2)
    stressed = with_features(base, xv_mark_dislocation_bps=16)
    unavailable = enrich_with_cross_venue(binance_snapshot(), None, history=[])
    policy = CrossVenuePolicy()

    assert policy.assess(confirming, jev_decision(confirming)).entry_quality_delta == 0.5
    conflict = policy.assess(conflicting, jev_decision(conflicting))
    assert conflict.status == "conflicting"
    assert conflict.entry_quality_delta == -1
    assert conflict.notional_multiplier == 0.5
    assert policy.assess(stressed, jev_decision(stressed)).notional_multiplier == 0
    missing = policy.assess(unavailable, jev_decision(unavailable))
    assert missing.status == "unavailable"
    assert missing.notional_multiplier == 1


def frame_at(at, *, funding, oi, spread):
    frame = build_hyperliquid_frame(
        {**hyperliquid_book(), "time": int(at.timestamp() * 1000)},
        {**asset_context(), "funding": str(funding), "openInterest": str(oi)},
        received_at=at,
    )
    return frame.model_copy(update={"spread_bps": spread})


def test_thresholds_use_only_prior_history_and_require_a_minimum_span():
    history = [
        frame_at(NOW - timedelta(days=8), funding=0.00001, oi=900, spread=1),
        frame_at(NOW - timedelta(hours=2), funding=0.00002, oi=950, spread=2),
        frame_at(NOW - timedelta(hours=1), funding=0.00003, oi=1000, spread=3),
        frame_at(NOW + timedelta(seconds=1), funding=1, oi=1, spread=999),
    ]

    thresholds = derive_cross_venue_thresholds(
        history, now=NOW, minimum_points=3
    )

    assert thresholds is not None
    assert thresholds.funding_abs_p95_bps_hour == pytest.approx(0.3)
    assert thresholds.spread_p99_bps == 3
    assert thresholds.oi_change_abs_p95_pct > 0
    assert derive_cross_venue_thresholds(
        history[1:], now=NOW, minimum_points=2
    ) is None


def test_crowded_funding_and_oi_reduce_size_without_creating_direction():
    base = enrich_with_cross_venue(
        binance_snapshot(),
        build_hyperliquid_frame(hyperliquid_book(), asset_context(), received_at=NOW),
        history=[],
    )
    crowded = with_features(
        base,
        hl_funding_bps_hour=0.5,
        hl_oi_change_1h_pct=5,
        hl_return_30s_pct=0,
        hl_book_imbalance_10bps=0,
    )
    policy = CrossVenuePolicy(thresholds=CrossVenueThresholds(
        funding_abs_p95_bps_hour=0.4,
        oi_change_abs_p95_pct=4,
        spread_p99_bps=10,
    ))

    result = policy.assess(crowded, jev_decision(crowded))

    assert result.status == "conflicting"
    assert result.entry_quality_delta == 0
    assert result.notional_multiplier == 0.5
    assert result.reason_codes == ("cross_venue_crowded",)
