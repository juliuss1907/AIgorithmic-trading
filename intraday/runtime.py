"""Composition root for safe stub paper-trading ticks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from intraday.contracts import Direction, FeatureSnapshot
from intraday.cross_venue import CrossVenuePolicy
from intraday.engine import IntradayEngine
from intraday.news import NewsIntelligence
from intraday.news_sources import NewsSource, enabled_sources, fetch_source
from intraday.providers import DecisionProvider, StubDecisionProvider
from intraday.store import IntradayStore


def run_once(
    *,
    database: str | Path,
    snapshot: FeatureSnapshot,
    direction: Direction = Direction.HOLD,
    initial_equity: float = 10_000,
    cross_venue_mode: str = "off",
    cross_venue_policy: CrossVenuePolicy | None = None,
    decision_provider: DecisionProvider | None = None,
    now: datetime | None = None,
) -> dict:
    store = IntradayStore(database)
    engine = IntradayEngine(
        store,
        decision_provider or StubDecisionProvider(direction=direction),
        initial_equity=initial_equity,
        cross_venue_mode=cross_venue_mode,
        cross_venue_policy=cross_venue_policy,
    )
    for command in store.list_commands(status="pending"):
        try:
            if command["kind"] == "pause_entries":
                engine.pause_entries("operator_pause", now=now or snapshot.built_at)
            elif command["kind"] == "resume_entries":
                engine.resume_entries(now=now or snapshot.built_at)
            elif command["kind"] == "rollback":
                store.rollback_champion(now=now or snapshot.built_at)
                engine.reload_active_rule()
            elif command["kind"] == "toggle_notifications":
                engine.notifications_enabled = not engine.notifications_enabled
                store.save_runtime_state(
                    engine._runtime_state(), updated_at=now or snapshot.built_at
                )
            elif command["kind"] == "flatten":
                engine.manual_flatten(
                    snapshot,
                    command_id=command["id"],
                    now=now or snapshot.built_at,
                )
            else:
                continue
        except (ValueError, KeyError):
            store.finish_command(command["id"], status="rejected")
        else:
            store.finish_command(command["id"], status="applied")
    result = engine.run_tick(snapshot, now=now or datetime.now(timezone.utc))
    return {
        "tick_id": result.decision.tick_id,
        "direction": result.decision.direction.value,
        "confidence": result.decision.direction_confidence,
        "gate": result.gate.outcome,
        "reasons": result.gate.reason_codes,
        "paper_fill": result.fill is not None,
        "tranches": result.position.tranches,
        "equity": engine.portfolio.equity(result.position.mark_price),
        "notifications_enabled": engine.notifications_enabled,
        "cross_venue_mode": result.gate.cross_venue_mode,
        "cross_venue_status": result.gate.cross_venue_status,
        "cross_venue_applied": result.gate.cross_venue_applied,
        "entry_quality_adjustment": result.gate.entry_quality_adjustment,
        "notional_multiplier": result.gate.notional_multiplier,
        "execution_enabled": False,
    }


def run_news_cycle(
    store: IntradayStore,
    *,
    now: datetime | None = None,
    sources: tuple[NewsSource, ...] | None = None,
    fetcher=fetch_source,
) -> dict:
    now = now or datetime.now(timezone.utc)
    runtime = store.load_runtime_state() or {}
    pause_value = runtime.get("news_pause_until")
    pause_until = datetime.fromisoformat(pause_value) if pause_value else None
    intelligence = NewsIntelligence(
        seed_events=store.list_news_events(limit=1000),
        pause_until=pause_until,
    )
    events = []
    failures = []
    for source in sources or enabled_sources():
        try:
            events.extend(fetcher(source, received_at=now))
        except Exception:
            failures.append(source.source_id)
    result = intelligence.ingest(events, now=now)
    store.record_news(result)
    runtime["news_pause_until"] = (
        result.pause_until.isoformat() if result.pause_until else None
    )
    store.save_runtime_state(runtime, updated_at=now)
    return {
        "accepted_events": len(result.accepted_events),
        "clusters": len(result.clusters),
        "verified_clusters": sum(cluster.verified for cluster in result.clusters),
        "pause_until": runtime["news_pause_until"],
        "failed_sources": failures,
    }
