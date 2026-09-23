"""Composition root for safe stub paper-trading ticks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from intraday.contracts import DecisionScope, Direction, FeatureSnapshot, ProviderRole
from intraday.cross_venue import CrossVenuePolicy
from intraday.engine import IntradayEngine
from intraday.news import NewsIntelligence
from intraday.news_sources import NewsSource, enabled_sources, fetch_source
from intraday.llm_pipeline import (
    LLMAnalysisPipeline,
    StructuredLLMError,
    active_llm_client,
    should_generate_scoped_rule,
)
from intraday.provider_profiles import ProviderSecretStore
from intraday.provider_client import ProviderPreflightClient
from intraday.providers import DecisionProvider, StubDecisionProvider
from intraday.parent_runtime import flatten_parent_paper_positions
from intraday.portfolio_coordinator import apply_operator_command
from intraday.store import IntradayStore


def process_pending_commands(
    store: IntradayStore,
    *,
    now: datetime,
    engine: IntradayEngine | None = None,
    snapshot: FeatureSnapshot | None = None,
    secret_store: ProviderSecretStore | None = None,
    preflight_client: ProviderPreflightClient | None = None,
) -> None:
    for command in store.list_commands(status="pending"):
        result = None
        try:
            payload = json.loads(command["payload_json"]) if command["payload_json"] else {}
            if command["kind"] == "provider_test":
                if secret_store is None:
                    raise ValueError("provider secret store is unavailable")
                profile_id = payload["profile_id"]
                preflight = (preflight_client or ProviderPreflightClient()).test(
                    secret_store.get(profile_id)
                )
                store.record_provider_test(
                    profile_id,
                    status=preflight.status,
                    tested_at=now,
                    latency_ms=preflight.latency_ms,
                    error_code=preflight.error_code,
                )
                result = {
                    "profile_id": profile_id,
                    "status": preflight.status,
                    "latency_ms": preflight.latency_ms,
                    "error_code": preflight.error_code,
                    "provider_request_id": preflight.provider_request_id,
                }
                if preflight.status != "ok":
                    store.finish_command(
                        command["id"], status="rejected", result=result,
                        error_code=preflight.error_code, applied_at=now,
                    )
                    continue
            elif command["kind"] == "provider_activate":
                role = ProviderRole(payload["role"])
                profile_id = payload["profile_id"]
                if secret_store is None:
                    raise ValueError("provider secret store is unavailable")
                credential = secret_store.get(profile_id)
                profile = store.provider_profile(profile_id)
                if profile is None or credential.profile.fingerprint != profile.fingerprint:
                    raise ValueError("provider profile secret mismatch")
                result = store.activate_provider(
                    role, profile_id, actor=command["actor"], now=now
                )
            elif command["kind"] == "provider_deactivate":
                role = ProviderRole(payload["role"])
                store.deactivate_provider(role)
                result = {"role": role.value, "active": False}
            elif command["kind"] in {
                "portfolio_pause", "portfolio_resume", "portfolio_flatten"
            }:
                parent = store.load_parent_portfolio_state()
                if parent is None:
                    raise ValueError("parent paper portfolio is not initialized")
                action = command["kind"].removeprefix("portfolio_")
                if action == "flatten":
                    parent = flatten_parent_paper_positions(
                        store,
                        parent,
                        now=now,
                        bid=snapshot.bid if snapshot is not None else None,
                        ask=snapshot.ask if snapshot is not None else None,
                        actor=command["actor"],
                    )
                else:
                    parent = apply_operator_command(parent, action, now=now)
                    store.save_parent_portfolio_state(
                        parent, event_kind=action, actor=command["actor"]
                    )
                result = {
                    "action": action,
                    "entries_paused": parent.entries_paused,
                    "spot_notional": parent.spot_notional,
                    "perp_notional": parent.perp_notional,
                }
            elif engine is None or snapshot is None:
                continue
            elif command["kind"] == "pause_entries":
                engine.pause_entries("operator_pause", now=now)
            elif command["kind"] == "resume_entries":
                engine.resume_entries(now=now)
            elif command["kind"] == "rollback":
                store.rollback_champion(now=now)
                engine.reload_active_rule()
            elif command["kind"] == "toggle_notifications":
                engine.notifications_enabled = not engine.notifications_enabled
                store.save_runtime_state(engine._runtime_state(), updated_at=now)
            elif command["kind"] == "flatten":
                engine.manual_flatten(snapshot, command_id=command["id"], now=now)
            else:
                raise ValueError("unsupported pending command")
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            store.finish_command(
                command["id"], status="rejected",
                error_code=type(error).__name__, applied_at=now,
            )
        else:
            store.finish_command(
                command["id"], status="applied", result=result, applied_at=now
            )


def run_once(
    *,
    database: str | Path,
    snapshot: FeatureSnapshot,
    direction: Direction = Direction.HOLD,
    initial_equity: float = 10_000,
    cross_venue_mode: str = "off",
    cross_venue_policy: CrossVenuePolicy | None = None,
    decision_provider: DecisionProvider | None = None,
    secret_store: ProviderSecretStore | None = None,
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
    process_pending_commands(
        store,
        now=now or snapshot.built_at,
        engine=engine,
        snapshot=snapshot,
        secret_store=secret_store,
    )
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


def run_analysis_cycle(
    store: IntradayStore,
    secret_store: ProviderSecretStore,
    *,
    now: datetime | None = None,
    client_factory=None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        client = active_llm_client(
            store, secret_store, client_factory=client_factory
        )
    except StructuredLLMError as error:
        return {"status": "degraded", "error_code": error.code}
    if client is None:
        return {"status": "skipped", "reason": "no_active_llm"}
    snapshot = store.latest_snapshot()
    if snapshot is None:
        return {"status": "skipped", "reason": "no_market_snapshot"}
    try:
        generate_scopes = {
            scope
            for scope in DecisionScope
            if should_generate_scoped_rule(store, scope=scope, now=now)
        }
        result = LLMAnalysisPipeline(client, store).run_scoped(
            snapshot,
            now=now,
            generate_scopes=generate_scopes,
        )
    except StructuredLLMError as error:
        return {"status": "degraded", "error_code": error.code}
    utc_now = now.astimezone(timezone.utc)
    day_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_cost = store.model_cost_since(day_start)
    candidate_ids = {
        candidate.scope.value: candidate.rule_id for candidate in result.candidates
    }
    return {
        "status": "ok",
        "thesis_id": result.bundle.thesis_id,
        "candidate_ids": candidate_ids,
        "daily_cost_usd": daily_cost,
        "cost_warning": daily_cost > 2,
        "rule_lifecycle": {
            scope.value: (
                "queued_for_soak" if scope.value in candidate_ids else "unchanged"
            )
            for scope in DecisionScope
        },
    }
