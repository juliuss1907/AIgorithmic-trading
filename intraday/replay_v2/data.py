"""Materialize a consistent read-only dataset; no collection or lifecycle calls."""

from __future__ import annotations

from contextlib import nullcontext

from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from intraday.contracts import FeatureSnapshot, JevDecision, ModelCallRecord
from intraday.providers import JevDecisionProvider
from intraday.replay_v2.contracts import (
    Candle, QuotePoint, RecordedDecision, ReplayConfig, ReplayDataset, utc,
)
from intraday.store import IntradayStore


REQUIRED_PERP = ("candles", "order_book", "premium", "open_interest", "long_short_ratio")


def _recorded_decision(row, snapshots, calls, profiles, config):
    """Resolve immutable historical call identity, never today's provider assignment."""
    at = utc(datetime.fromisoformat(row["timestamp"]))
    state = json.loads(row["state_snapshot"])
    snap = snapshots[state["snapshot_id"]]
    if snap.symbol != config.symbol or snap.market != "binance_usdm_perp" or snap.feature_schema_version != "2":
        raise ValueError("signal snapshot scope mismatch")
    expected_state = json.loads(json.dumps(JevDecisionProvider._state(snap, config.scope)))
    if state != expected_state:
        raise ValueError("signal state differs from immutable snapshot")
    tick = f"{config.symbol}:{config.scope.value}:{int(at.timestamp()*1000)}"
    for profile_id, fingerprint in profiles:
        model_ref = f"{profile_id}@{fingerprint[:12]}"
        decision_id = hashlib.sha256(
            f"{model_ref}:{tick}:{snap.checksum}:numeric_v1:primary:{row['experiment_pair_id'] or '-'}".encode()
        ).hexdigest()[:24]
        if decision_id != row["decision_id"]:
            continue
        call_id = hashlib.sha256(f"perp_intraday_entry:{tick}:{fingerprint}:success:None".encode()).hexdigest()[:32]
        call = calls.get(call_id)
        if not call or call.profile_id != profile_id or call.profile_fingerprint != fingerprint:
            continue
        available = max(at, utc(snap.built_at), utc(snap.event_time), utc(call.completed_at))
        if available >= config.end:
            raise ValueError("decision not available inside replay window")
        parsed = JevDecisionProvider._parse_answers({"answers": json.loads(row["jev_answers"])})
        decision = JevDecision(decision_id=decision_id, tick_id=tick, snapshot_id=snap.snapshot_id,
            direction=parsed[0], direction_confidence=parsed[1], regime=parsed[2], toxic_flow=parsed[3],
            entry_quality=parsed[4], risk_level=parsed[5], model_ref=model_ref, created_at=at)
        valid = all(snap.freshness.get(name) is True and f"{name}_missing" not in snap.quality_flags
                    for name in REQUIRED_PERP)
        reference = Decimal(str(snap.features["reference_price"]))
        if not reference.is_finite() or reference <= 0:
            raise ValueError("invalid recorded reference price")
        return RecordedDecision(decision, available, reference, valid, call.call_id)
    raise ValueError("historical model-backed provenance unavailable")


def _perp_data(store, config):
    snapshots = store.list_snapshots_between(config.start - timedelta(seconds=45), config.end,
                                            symbol=config.symbol)
    indexed = {snap.snapshot_id: snap for snap in snapshots}
    quotes, limitations = [], []
    missing_mark = 0
    for snap in snapshots:
        at = max(utc(snap.event_time), utc(snap.built_at))
        if not config.start <= at < config.end:
            continue
        if any(value is not None and not Decimal(str(value)).is_finite() for value in snap.features.values()):
            raise ValueError("non-finite recorded market features")
        mark = snap.features.get("mark_price")
        if mark is None or mark <= 0:
            missing_mark += 1
            continue
        quotes.append(QuotePoint(at=at, event_time=snap.event_time, snapshot_id=snap.snapshot_id,
            bid=str(snap.bid), ask=str(snap.ask), mark=str(mark),
            fresh=snap.freshness.get("order_book") is True and "order_book_missing" not in snap.quality_flags))
    with store._connect() as c:
        rows = c.execute(
            "SELECT * FROM signals WHERE symbol=? AND scope=? AND market='binance_usdm_perp' "
            "AND feature_schema_version='2' AND state_variant='numeric_v1' AND decision_mode='primary' "
            "AND julianday(timestamp)>=julianday(?) AND julianday(timestamp)<julianday(?) ORDER BY timestamp,id",
            (config.symbol, config.scope.value, config.start.isoformat(), config.end.isoformat())).fetchall()
        call_rows = c.execute(
            "SELECT status,payload_json FROM model_calls WHERE role='jev' AND status='success' "
            "AND julianday(started_at)>=julianday(?) AND julianday(started_at)<julianday(?)",
            ((config.start - timedelta(seconds=45)).isoformat(), config.end.isoformat())).fetchall()
    calls = {}
    for row in call_rows:
        call = ModelCallRecord.model_validate_json(row["payload_json"])
        if call.status == "success" and call.workflow == "perp_intraday_entry":
            calls[call.call_id] = call
    profiles = sorted({(call.profile_id, call.profile_fingerprint) for call in calls.values()})
    decisions, invalid = [], 0
    for row in rows:
        try:
            decisions.append(_recorded_decision(row, indexed, calls, profiles, config))
        except (ValueError, KeyError, TypeError):
            invalid += 1
    if invalid:
        limitations.append(f"unverified_recorded_decisions:{invalid}")
    if missing_mark:
        limitations.append(f"snapshots_without_mark:{missing_mark}")
    return tuple(sorted(quotes, key=lambda q: (q.at, q.event_time, q.snapshot_id))), tuple(
        sorted(decisions, key=lambda d: (d.available_at, d.decision.decision_id))), tuple(limitations)


def load_dataset(database: str | Path, config: ReplayConfig, *, reader=None) -> ReplayDataset:
    store = reader or IntradayStore(database, read_only=True)
    if not store.read_only or store.database != Path(database).resolve():
        raise ValueError("replay dataset requires its matching read-only source")
    context = nullcontext(store) if store._read_snapshot_connection is not None else store.read_snapshot()
    with context:
        rule = store.load_scoped_rule(config.rule_id)
        if not rule or rule.symbol != config.symbol or rule.scope != config.scope:
            raise ValueError("research rule must belong to the requested coin and scope")
        if config.scope not in store.asset_spec(config.symbol).enabled_scopes:
            raise ValueError("market scope is not enabled for this registered coin")
        evaluation = store.latest_scoped_rule_evaluation(rule.rule_id, kind="replay")
        reference = evaluation.model_dump(mode="json") if evaluation else None
        if config.market == "spot" and getattr(rule.parameters, "entry_profile", "donchian_v1") == "setup2_v1":
            from intraday.setup2_store import coin_anchor, list_bars, profile_start
            anchor = coin_anchor(store, config.symbol)
            candles = tuple(Candle(opened_at=b.opened_at, available_at=b.available_at, open=b.open, high=b.high,
                                   low=b.low, close=b.close, volume=b.volume)
                            for b in list_bars(store, config.symbol, "spot", "4h", since=anchor, until=config.end))
            profile = tuple(list_bars(store, config.symbol, "spot", "15m", since=profile_start(anchor),
                                      until=config.end))
            return ReplayDataset(rule=rule, candles=candles, profile_candles=profile, indicator_anchor=anchor,
                                 v1_reference=reference)
        if config.market == "spot":
            candles = tuple(Candle.from_row(row) for row in store.list_asset_candles(
                config.symbol, "4h", as_of=config.end))
            candles = tuple(candle for candle in candles if candle.available_at <= config.end)
            if any(a.opened_at >= b.opened_at for a, b in zip(candles, candles[1:])):
                raise ValueError("recorded candles must be strictly chronological")
            return ReplayDataset(rule=rule, candles=candles, v1_reference=reference)
        quotes, decisions, limitations = _perp_data(store, config)
        return ReplayDataset(rule=rule, quotes=quotes, decisions=decisions,
                             limitations=limitations, v1_reference=reference)
