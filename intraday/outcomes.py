"""Forward signal outcomes computed only from immutable recorded snapshots."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from intraday.contracts import DecisionScope, Direction


DEFAULT_HORIZONS = {
    DecisionScope.PERP_INTRADAY: (300, 900, 3600),
    DecisionScope.SPOT_DAILY: (86_400, 259_200, 604_800),
}


class SignalOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: str = Field(min_length=16, max_length=64)
    signal_id: int = Field(gt=0)
    horizon_sec: int = Field(gt=0)
    observed_at: datetime
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    forward_return_pct: float
    max_upside_pct: float
    max_downside_pct: float
    directional_return_pct: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    sample_count: int = Field(gt=0)
    coverage_pct: float = Field(ge=0, le=100)
    price_source: str = "binance_usdm_snapshot"

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("outcome timestamp must be timezone-aware")
        return value


def _price(snapshot) -> float:
    value = snapshot.features.get("mark_price", snapshot.features.get("price"))
    if value is None or value <= 0:
        raise ValueError("snapshot has no positive mark price")
    return float(value)


def evaluate_pending_outcomes(
    store,
    *,
    now: datetime,
    horizons: set[int] | None = None,
) -> dict[str, int]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("outcome evaluation time must be timezone-aware")
    selected = horizons or {
        horizon for values in DEFAULT_HORIZONS.values() for horizon in values
    }
    completed = 0
    pending = 0
    for scope, scope_horizons in DEFAULT_HORIZONS.items():
        for horizon in scope_horizons:
            if horizon not in selected:
                continue
            matured_before = now - timedelta(seconds=horizon + 90)
            for signal in store.list_signals_missing_outcome(
                scope=scope, horizon_sec=horizon, matured_before=matured_before
            ):
                started_at = datetime.fromisoformat(signal["timestamp"])
                target = started_at + timedelta(seconds=horizon)
                snapshots = store.list_snapshots_between(
                    started_at, target + timedelta(seconds=90)
                )
                entry = next(
                    (
                        item for item in snapshots
                        if 0 <= (item.event_time - started_at).total_seconds() <= 90
                    ),
                    None,
                )
                exit_snapshot = next(
                    (
                        item for item in snapshots
                        if 0 <= (item.event_time - target).total_seconds() <= 90
                    ),
                    None,
                )
                if entry is None or exit_snapshot is None:
                    pending += 1
                    continue
                path = [item for item in snapshots if item.event_time <= exit_snapshot.event_time]
                expected = horizon // 30 + 1
                coverage = min(100.0, len(path) / expected * 100)
                if coverage < 90:
                    pending += 1
                    continue
                entry_price = _price(entry)
                exit_price = _price(exit_snapshot)
                returns = [(_price(item) / entry_price - 1) * 100 for item in path]
                forward = (exit_price / entry_price - 1) * 100
                choice = signal["direction"]
                sign = (
                    1
                    if choice in {Direction.BUY.value, Direction.STRONG_BUY.value}
                    else -1
                    if choice in {Direction.SELL.value, Direction.STRONG_SELL.value}
                    else None
                )
                signed = [value * sign for value in returns] if sign is not None else []
                identity = hashlib.sha256(
                    f"{signal['id']}:{horizon}:{exit_snapshot.snapshot_id}".encode()
                ).hexdigest()[:32]
                store.record_signal_outcome(
                    SignalOutcome(
                        outcome_id=identity,
                        signal_id=signal["id"],
                        horizon_sec=horizon,
                        observed_at=exit_snapshot.event_time,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        forward_return_pct=forward,
                        max_upside_pct=max(returns),
                        max_downside_pct=min(returns),
                        directional_return_pct=(forward * sign if sign is not None else None),
                        mfe_pct=(max(signed) if signed else None),
                        mae_pct=(min(signed) if signed else None),
                        sample_count=len(path),
                        coverage_pct=coverage,
                    )
                )
                completed += 1
    return {"completed": completed, "pending": pending}
