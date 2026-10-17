"""Deterministic UTC cadence slots for restart-safe worker jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def cadence_slot(now: datetime, interval_seconds: float) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scheduler time must be timezone-aware")
    if interval_seconds <= 0:
        raise ValueError("scheduler interval must be positive")
    utc = now.astimezone(timezone.utc)
    seconds = int(interval_seconds)
    epoch = int(utc.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def claim_cadence(
    store,
    job_name: str,
    now: datetime,
    interval_seconds: float,
    *,
    retry_error_codes: tuple[str, ...] = (),
    retry_window: timedelta = timedelta(minutes=30),
) -> datetime | None:
    """Claim the current slot. A slot that failed with a transient error (``retry_error_codes``) is retried
    on the next loop while it is younger than ``retry_window``; any other failure waits for the next slot."""
    slot = cadence_slot(now, interval_seconds)
    retry = retry_error_codes if now - slot <= retry_window else ()
    return slot if store.claim_scheduler_run(job_name, slot, started_at=now, retry_error_codes=retry) else None
