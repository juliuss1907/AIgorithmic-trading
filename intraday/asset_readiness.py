"""Read-only per-asset rule/venue readiness. Never an execution authorization."""

from datetime import datetime, timedelta, timezone

from intraday.assets import ticker_symbol
from intraday.contracts import DecisionScope
from intraday.perp_bootstrap_lifecycle import preview_perp_bootstrap, preview_perp_post_replay
from intraday.spot_4h_lifecycle import MIN_BARS, WIDTH, preview_spot_4h_soak
from intraday.store import IntradayStore


SCOPES = {"spot": DecisionScope.SPOT_4H, "perp": DecisionScope.PERP_INTRADAY}


def build_asset_readiness(database, *, symbol=None, market=None, now=None):
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None or market not in {None, "spot", "perp"}:
        raise ValueError("readiness requires an aware clock and spot/perp market")
    now = now.astimezone(timezone.utc)
    reader = IntradayStore(database, read_only=True)
    with reader.read_snapshot():
        catalog = reader.asset_catalog()
        if symbol is not None:
            symbol = ticker_symbol(symbol)
            if symbol not in catalog:
                raise ValueError("asset is not registered")
        rows = []
        for spec in catalog.values():
            if symbol is not None and spec.symbol != symbol:
                continue
            for selected_market, scope in SCOPES.items():
                if scope not in spec.enabled_scopes or market not in {None, selected_market}:
                    continue
                rows.append(_asset_row(reader, spec.symbol, selected_market, scope, now))
    return {"report_schema_version": "1", "generated_at": now.isoformat(), "rows": rows}


def _saved(reader, rule_id, kind):
    value = reader.latest_scoped_rule_evaluation(rule_id, kind=kind) if rule_id else None
    return value.model_dump(mode="json") if value else None


def _asset_row(reader, symbol, market, scope, now):
    registry = reader.scoped_rule_registry(scope, symbol=symbol)
    rules = reader.list_scoped_rules(scope, symbol=symbol)
    active = [r for r in rules if r["status"] in {"queued", "replay_passed", "challenger"}]
    candidate = next((r for r in active if r["id"] == registry.get("challenger_id")), None)
    candidate = candidate or (active[0] if len(active) == 1 else None)
    rejected = next((r for r in rules if r["status"] == "rejected"), None)
    selected = candidate or rejected
    lifecycle = next((item for item in reader.list_asset_lifecycles(symbol) if item.scope is scope), None)
    with reader._connect() as connection:
        route = connection.execute(
            "SELECT venue,environment,instrument,updated_at FROM asset_venue_routes WHERE symbol=? AND market=?",
            (symbol, market),
        ).fetchone()
    rule_id = selected["id"] if selected else registry.get("champion_id")
    row = {
        "symbol": symbol, "market": market, "scope": scope.value,
        "lifecycle": lifecycle.stage.value if lifecycle else "unregistered",
        "phase": "no_rule", "champion_id": registry.get("champion_id"),
        "candidate_id": selected["id"] if selected else None,
        "candidate_status": selected["status"] if selected else None,
        "replay": _saved(reader, rule_id, "replay"), "soak": _saved(reader, rule_id, "soak"),
        "preview": None, "gates": [], "blockers": [], "next_action": "bootstrap",
        "started_at": None, "replay_cutoff": None, "earliest_evaluation_at": None,
        "legacy_champion": False, "venue": dict(route) if route else None,
        "venue_blockers": [] if route else ["venue_not_selected"],
    }
    if row["lifecycle"] == "disabled":
        row.update(phase="disabled", blockers=["scope_disabled"], next_action="none")
    elif len(active) > 1 or (registry.get("challenger_id") and candidate is None):
        row.update(phase="inconsistent", blockers=["inconsistent_rule_registry"], next_action="investigate")
    elif candidate:
        _candidate_progress(reader, candidate, registry, row, now)
    elif row["champion_id"]:
        row.update(phase="champion", next_action="none")
        champion_soak = _saved(reader, row["champion_id"], "soak")
        row["legacy_champion"] = champion_soak is None
    elif rejected:
        latest = row["soak"] or row["replay"]
        row.update(phase="rejected", next_action="await_proposal",
                   blockers=latest["reason_codes"] if latest else ["candidate_rejected"])
    else:
        row["blockers"] = ["baseline_required"]
    return row


def _candidate_progress(reader, candidate, registry, row, now):
    rule = reader.load_scoped_rule(candidate["id"])
    expected_parent = registry.get("champion_id") or "bootstrap"
    if (rule is None or rule.symbol != row["symbol"] or rule.scope.value != row["scope"]
            or rule.parent_rule_id != expected_parent or rule.created_at > now):
        row.update(phase="inconsistent", blockers=["candidate_lineage_mismatch"], next_action="investigate")
        return
    if any(item and datetime.fromisoformat(item["evaluated_at"]) > now
           for item in (row["replay"], row["soak"])):
        row.update(phase="inconsistent", blockers=["evaluation_in_future"], next_action="investigate")
        return
    if candidate["status"] != "challenger":
        if row["market"] == "perp":
            row.update(phase="awaiting_decision_soak", next_action="start_decision_soak")
        else:
            _spot_replay_progress(reader, row, now)
        return
    try:
        if row["market"] == "spot":
            preview = preview_spot_4h_soak(reader, candidate["id"], now=now)
            phase, hours = "spot_soak", 14 * 24
        elif row["replay"] and row["replay"]["status"] == "pass":
            preview = preview_perp_post_replay(reader, candidate["id"], now=now)
            phase, hours = "post_replay_validation", 72
            row["replay_cutoff"] = row["replay"]["evaluated_at"]
        else:
            preview = preview_perp_bootstrap(reader, candidate["id"], now=now)
            phase, hours = "decision_soak", 14 * 24
        _soak_progress(row, preview, phase, hours, now)
    except (ValueError, KeyError, TypeError):
        row.update(phase="inconsistent", blockers=["campaign_evidence_inconsistent"], next_action="investigate")


def _gate(key, value, required, unit, *, maximum=False, strict=False):
    if value is None:
        status = "unknown"
    else:
        passed = value < required if maximum and strict else value <= required if maximum else value > required if strict else value >= required
        status = "pass" if passed else "wait"
    return {"key": key, "value": value, "required": required, "unit": unit,
            "comparison": "<" if maximum and strict else "<=" if maximum else ">" if strict else ">=", "status": status}


def _spot_replay_progress(reader, row, now):
    candles = reader.list_asset_candles(row["symbol"], "4h", as_of=now)
    span = (int(candles[-1][0]) - int(candles[0][0])) // WIDTH + 1 if candles else 0
    coverage = min(1., len(candles) / span) if span else 0.
    age = (now.timestamp() * 1000 - int(candles[-1][6])) / 1000 if candles else None
    row["gates"] = [
        _gate("history_days", len(candles) / 6, MIN_BARS / 6, "days"),
        _gate("history_coverage", coverage, .99, "fraction"),
        _gate("latest_candle_age", age, WIDTH / 1000 + 300, "seconds", maximum=True),
    ]
    replay = row["replay"]
    if row["candidate_status"] == "replay_passed":
        if replay and replay["status"] == "pass":
            row.update(phase="awaiting_spot_soak", next_action="start_soak")
        else:
            row.update(phase="inconsistent", blockers=["passing_replay_required"], next_action="investigate")
        return
    missing = [gate["key"] for gate in row["gates"] if gate["status"] != "pass"]
    row.update(phase="history" if missing else "awaiting_replay",
               blockers=missing + ["persisted_replay_required"],
               next_action="backfill_history" if missing else "run_replay")


def _soak_progress(row, preview, phase, hours, now):
    values = preview.values
    started = values["started_at"]
    if started > now:
        raise ValueError("campaign start is in the future")
    row.update(phase=phase, started_at=started.isoformat(),
               earliest_evaluation_at=(started + timedelta(hours=hours)).isoformat(),
               preview={key: values[key] for key in (
                   "kind", "status", "sample_count", "coverage", "challenger_score", "metrics")},
               blockers=list(preview.blockers), next_action="wait")
    metrics = values["metrics"]
    row["gates"] = [
        _gate("elapsed_hours", (now - started).total_seconds() / 3600, hours, "hours"),
        _gate("heartbeat_coverage", values["coverage"] if row["market"] == "spot" else metrics["heartbeat_coverage"], .95, "fraction"),
        _gate("matured_setups" if row["market"] == "spot" else "matured_outcomes",
              metrics["matured_setups"] if row["market"] == "spot" else metrics["outcomes"],
              6 if row["market"] == "spot" else 100, "count"),
        _gate("after_cost_score", values["challenger_score"], 0, "percent", strict=True),
        _gate("hard_risk_violations", preview.hard_risk_violations, 0, "count", maximum=True),
    ]
    if row["market"] == "perp":
        row["gates"].append(_gate("outcome_coverage", values["coverage"], .95, "fraction"))
    if values["status"] == "reject":
        row["next_action"] = "investigate"
    elif values["status"] == "pass":
        if phase == "decision_soak":
            row["next_action"] = "run_replay"
        elif row["soak"] and row["soak"]["status"] == "pass":
            row.update(phase="awaiting_promotion", next_action="request_promotion")
        else:
            row["next_action"] = "run_evaluation"
