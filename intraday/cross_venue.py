"""Perp-DEX normalization and deterministic cross-venue feature engineering."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from intraday.contracts import (
    CrossVenueAssessment,
    Direction,
    FeatureSnapshot,
    JevDecision,
    VenueMarketFrame,
)


DEPTH_BUCKETS_BPS = (5, 10, 25)
CROSS_VENUE_FEATURES = (
    "hl_mark_price",
    "hl_oracle_price",
    "hl_funding_bps_hour",
    "hl_oi_usd",
    "hl_oi_change_5m_pct",
    "hl_oi_change_1h_pct",
    "hl_return_30s_pct",
    "hl_spread_bps",
    "hl_book_imbalance_5bps",
    "hl_book_imbalance_10bps",
    "hl_book_imbalance_25bps",
    "hl_bid_depth_usd_5bps",
    "hl_bid_depth_usd_10bps",
    "hl_bid_depth_usd_25bps",
    "hl_ask_depth_usd_5bps",
    "hl_ask_depth_usd_10bps",
    "hl_ask_depth_usd_25bps",
    "xv_mark_dislocation_bps",
    "xv_funding_spread_bps_hour",
    "xv_book_imbalance_delta_10bps",
)


@dataclass(frozen=True)
class CrossVenueThresholds:
    funding_abs_p95_bps_hour: float
    oi_change_abs_p95_pct: float
    spread_p99_bps: float


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def derive_cross_venue_thresholds(
    frames: list[VenueMarketFrame],
    *,
    now: datetime,
    minimum_days: int = 7,
    minimum_points: int = 1000,
) -> CrossVenueThresholds | None:
    """Build causal thresholds from frames strictly older than the evaluation time."""
    history = sorted(
        (frame for frame in frames if frame.event_time < now),
        key=lambda frame: frame.event_time,
    )
    if len(history) < minimum_points:
        return None
    if now - history[0].event_time < timedelta(days=minimum_days):
        return None
    oi_changes = []
    previous_index = 0
    for current in history:
        target = current.event_time - timedelta(hours=1)
        while (
            previous_index + 1 < len(history)
            and history[previous_index + 1].event_time <= target
        ):
            previous_index += 1
        previous = history[previous_index]
        if previous.event_time <= target and previous.open_interest_usd > 0:
            oi_changes.append(abs(current.open_interest_usd / previous.open_interest_usd - 1) * 100)
    if not oi_changes:
        return None
    return CrossVenueThresholds(
        funding_abs_p95_bps_hour=_nearest_rank(
            [abs(frame.funding_bps_hour) for frame in history], 0.95
        ),
        oi_change_abs_p95_pct=_nearest_rank(oi_changes, 0.95),
        spread_p99_bps=_nearest_rank([frame.spread_bps for frame in history], 0.99),
    )


def _book_side(levels: list[dict]) -> list[tuple[float, float]]:
    return [(float(level["px"]), float(level["sz"])) for level in levels]


def build_hyperliquid_frame(
    book: dict,
    asset_context: dict,
    *,
    received_at: datetime,
    metadata_received_at: datetime | None = None,
) -> VenueMarketFrame:
    """Normalize one Hyperliquid BTC book plus current perpetual context."""
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    levels = book.get("levels") or []
    if len(levels) != 2 or not levels[0] or not levels[1]:
        raise ValueError("order book must contain both sides")
    bids, asks = _book_side(levels[0]), _book_side(levels[1])
    bid, ask = bids[0][0], asks[0][0]
    if bid > ask:
        raise ValueError("order book is crossed")
    mid = (bid + ask) / 2
    bid_depth: dict[str, float] = {}
    ask_depth: dict[str, float] = {}
    imbalance: dict[str, float] = {}
    for bucket in DEPTH_BUCKETS_BPS:
        lower = mid * (1 - bucket / 10_000)
        upper = mid * (1 + bucket / 10_000)
        bid_value = sum(price * size for price, size in bids if price >= lower)
        ask_value = sum(price * size for price, size in asks if price <= upper)
        total = bid_value + ask_value
        key = str(bucket)
        bid_depth[key] = bid_value
        ask_depth[key] = ask_value
        imbalance[key] = (bid_value - ask_value) / total if total else 0.0
    mark = float(asset_context["markPx"])
    event_time = datetime.fromtimestamp(float(book["time"]) / 1000, tz=timezone.utc)
    return VenueMarketFrame.create(
        venue="hyperliquid",
        symbol="BTCUSDT",
        event_time=event_time,
        received_at=received_at,
        metadata_received_at=metadata_received_at or received_at,
        bid=bid,
        ask=ask,
        mark_price=mark,
        index_price=float(asset_context["oraclePx"]),
        funding_bps_hour=float(asset_context["funding"]) * 10_000,
        open_interest_usd=float(asset_context["openInterest"]) * mark,
        spread_bps=(ask - bid) / mid * 10_000,
        bid_depth_usd=bid_depth,
        ask_depth_usd=ask_depth,
        book_imbalance=imbalance,
    )


def _change_since(
    current: VenueMarketFrame,
    history: list[VenueMarketFrame],
    horizon: timedelta,
) -> float | None:
    target = current.event_time - horizon
    candidates = [frame for frame in history if frame.event_time <= target]
    if not candidates:
        return None
    previous = max(candidates, key=lambda frame: frame.event_time)
    if previous.open_interest_usd == 0:
        return None
    return (current.open_interest_usd / previous.open_interest_usd - 1) * 100


def _return_since(
    current: VenueMarketFrame,
    history: list[VenueMarketFrame],
    horizon: timedelta,
) -> float | None:
    target = current.event_time - horizon
    candidates = [frame for frame in history if frame.event_time <= target]
    if not candidates:
        return None
    previous = max(candidates, key=lambda frame: frame.event_time)
    return (current.mark_price / previous.mark_price - 1) * 100


def enrich_with_cross_venue(
    snapshot: FeatureSnapshot,
    frame: VenueMarketFrame | None,
    *,
    history: list[VenueMarketFrame],
    max_book_age_seconds: float = 2,
    binance_funding_interval_hours: float = 8,
) -> FeatureSnapshot:
    """Bind optional DEX evidence into a Binance decision snapshot.

    Hyperliquid is advisory: missing or stale evidence is represented numerically
    and never changes the Binance freshness/quality fields used by the hard gate.
    """
    features = dict(snapshot.features)
    usable = frame is not None
    if usable:
        age = (snapshot.built_at - frame.received_at).total_seconds()
        usable = -1 <= age <= max_book_age_seconds
    if not usable:
        features.update({name: None for name in CROSS_VENUE_FEATURES})
        features["xv_coverage_score"] = 0.0
    else:
        assert frame is not None
        binance_mark = float(features.get("mark_price") or features["price"])
        binance_funding = features.get("funding_rate")
        binance_funding_hour = (
            float(binance_funding) * 10_000 / binance_funding_interval_hours
            if binance_funding is not None else None
        )
        features.update({
            "hl_mark_price": frame.mark_price,
            "hl_oracle_price": frame.index_price,
            "hl_funding_bps_hour": frame.funding_bps_hour,
            "hl_oi_usd": frame.open_interest_usd,
            "hl_oi_change_5m_pct": _change_since(frame, history, timedelta(minutes=5)),
            "hl_oi_change_1h_pct": _change_since(frame, history, timedelta(hours=1)),
            "hl_return_30s_pct": _return_since(frame, history, timedelta(seconds=30)),
            "hl_spread_bps": frame.spread_bps,
            **{
                f"hl_book_imbalance_{bucket}bps": frame.book_imbalance[str(bucket)]
                for bucket in DEPTH_BUCKETS_BPS
            },
            **{
                f"hl_bid_depth_usd_{bucket}bps": frame.bid_depth_usd[str(bucket)]
                for bucket in DEPTH_BUCKETS_BPS
            },
            **{
                f"hl_ask_depth_usd_{bucket}bps": frame.ask_depth_usd[str(bucket)]
                for bucket in DEPTH_BUCKETS_BPS
            },
            "xv_mark_dislocation_bps": (frame.mark_price / binance_mark - 1) * 10_000,
            "xv_funding_spread_bps_hour": (
                frame.funding_bps_hour - binance_funding_hour
                if binance_funding_hour is not None else None
            ),
            "xv_book_imbalance_delta_10bps": (
                frame.book_imbalance["10"]
                - float(features.get("order_book_imbalance") or 0)
            ),
            "xv_coverage_score": 1.0,
        })
    return FeatureSnapshot.create(
        symbol=snapshot.symbol,
        market=snapshot.market,
        timeframe=snapshot.timeframe,
        feature_schema_version=snapshot.feature_schema_version,
        event_time=snapshot.event_time,
        built_at=snapshot.built_at,
        bid=snapshot.bid,
        ask=snapshot.ask,
        features=features,
        freshness=snapshot.freshness,
        quality_flags=snapshot.quality_flags,
    )


class CrossVenuePolicy:
    version = "cross-venue-v1"

    def __init__(
        self,
        *,
        directional_return_threshold_pct: float = 0.01,
        imbalance_threshold: float = 0.10,
        dislocation_stress_bps: float = 15,
        thresholds: CrossVenueThresholds | None = None,
    ):
        self.directional_return_threshold_pct = directional_return_threshold_pct
        self.imbalance_threshold = imbalance_threshold
        self.dislocation_stress_bps = dislocation_stress_bps
        self.thresholds = thresholds

    def assess(
        self,
        snapshot: FeatureSnapshot,
        decision: JevDecision,
    ) -> CrossVenueAssessment:
        features = snapshot.features
        coverage = float(features.get("xv_coverage_score") or 0)
        if coverage < 1:
            return self._result("unavailable", 0, 1, ("cross_venue_unavailable",), snapshot)
        dislocation = features.get("xv_mark_dislocation_bps")
        if dislocation is not None and abs(dislocation) >= self.dislocation_stress_bps:
            return self._result("stressed", 0, 0, ("cross_venue_stressed",), snapshot)
        if self.thresholds is not None:
            spread = features.get("hl_spread_bps")
            if spread is not None and spread >= self.thresholds.spread_p99_bps:
                return self._result(
                    "stressed", 0, 0, ("cross_venue_spread_stress",), snapshot
                )
            funding = features.get("hl_funding_bps_hour")
            oi_change = features.get("hl_oi_change_1h_pct")
            if (
                funding is not None
                and oi_change is not None
                and abs(funding) >= self.thresholds.funding_abs_p95_bps_hour
                and abs(oi_change) >= self.thresholds.oi_change_abs_p95_pct
            ):
                return self._result(
                    "conflicting", 0, 0.5, ("cross_venue_crowded",), snapshot
                )
        direction = {
            Direction.BUY: 1,
            Direction.STRONG_BUY: 1,
            Direction.SELL: -1,
            Direction.STRONG_SELL: -1,
        }.get(decision.direction)
        change = features.get("hl_return_30s_pct")
        imbalance = features.get("hl_book_imbalance_10bps")
        if direction is None or change is None or imbalance is None:
            return self._result("neutral", 0, 1, (), snapshot)
        return_sign = 1 if change >= self.directional_return_threshold_pct else (
            -1 if change <= -self.directional_return_threshold_pct else 0
        )
        book_sign = 1 if imbalance >= self.imbalance_threshold else (
            -1 if imbalance <= -self.imbalance_threshold else 0
        )
        if return_sign == direction and book_sign == direction:
            return self._result("confirming", 0.5, 1, ("cross_venue_confirming",), snapshot)
        if return_sign == -direction and book_sign == -direction:
            return self._result("conflicting", -1, 0.5, ("cross_venue_conflicting",), snapshot)
        return self._result("neutral", 0, 1, (), snapshot)

    def _result(self, status, delta, multiplier, reasons, snapshot):
        return CrossVenueAssessment(
            status=status,
            entry_quality_delta=delta,
            notional_multiplier=multiplier,
            reason_codes=reasons,
            policy_version=self.version,
            evaluated_at=snapshot.built_at,
        )
