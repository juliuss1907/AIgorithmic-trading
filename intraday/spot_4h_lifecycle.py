"""Independent UTC 4h Spot baseline and evidence gates; never enables fills."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from statistics import fmean

from intraday.asset_rule_lifecycle import _walk_forward
from intraday.assets import asset_spec
from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation
from intraday.rule_preview import RuleGatePreview
from intraday.replay_v2.compatibility import require_legacy_campaign
from intraday.spot_signal import BinanceSpotDailyClient, SPOT_INTERVAL_MS


SCOPE = DecisionScope.SPOT_4H
WIDTH = SPOT_INTERVAL_MS["4h"]
MIN_BARS = 365 * 6
ROUND_TRIP_COST_PCT = 0.30


def refresh_spot_4h_history(
    store, symbol: str, *, now: datetime,
    client: BinanceSpotDailyClient | None = None,
) -> None:
    symbol = store.asset_spec(symbol).symbol
    client = client or BinanceSpotDailyClient(asset_catalog=store.asset_catalog())
    now_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
    for interval, width in SPOT_INTERVAL_MS.items():
        start = (now_ms - 365 * 86_400_000) // width * width
        rows = client.backfill(
            symbol=symbol, interval=interval, start_time=start,
            end_time=now_ms, now=now,
        )
        store.record_asset_candles(symbol, interval, rows)


def bootstrap_spot_4h_rule(
    store, symbol: str, *, now: datetime,
    client: BinanceSpotDailyClient | None = None,
) -> ScopedRuleCandidate:
    symbol = store.asset_spec(symbol).symbol
    if store.load_active_scoped_rule(SCOPE, symbol=symbol):
        raise ValueError("asset already has a spot_4h champion")
    if store.has_open_scoped_rule_candidate(SCOPE, symbol=symbol):
        raise ValueError("asset already has an open candidate")
    refresh_spot_4h_history(store, symbol, now=now, client=client)
    candidate = ScopedRuleCandidate.create(
        rule_id=f"{symbol.lower()}-spot-4h-baseline-v1",
        parent_rule_id="bootstrap", thesis_id="deterministic-baseline",
        symbol=symbol, scope=SCOPE, parameters=SpotRuleParameters(),
        created_at=now, model_ref="deterministic/baseline",
        prompt_version="spot-4h-baseline-v1",
        rationale="UTC 4h Donchian 20/10 ATR14; native 8h and 1d context only.",
    )
    store.register_scoped_rule(candidate)
    return candidate


def spot_4h_history_progress(candles: list, *, now: datetime) -> dict:
    """Cheap history checks shared by readiness and replay; never runs a backtest."""
    span = ((int(candles[-1][0]) - int(candles[0][0])) // WIDTH + 1) if candles else 0
    coverage = min(1.0, len(candles) / span) if span else 0.0
    age = (int(now.timestamp() * 1000) - int(candles[-1][6])) / 1000 if candles else None
    waiting = []
    if len(candles) < MIN_BARS:
        waiting.append("minimum_365_days_4h_history")
    if coverage < .99:
        waiting.append("4h_candle_coverage_below_99pct")
    if age is not None and age > WIDTH / 1000 + 300:
        waiting.append("latest_4h_candle_stale")
    return {"bars": len(candles), "coverage": coverage,
            "latest_candle_age_seconds": age, "reason_codes": waiting}


def replay_spot_4h_rule(store, candidate_id: str, *, now: datetime) -> ScopedRuleEvaluation:
    require_legacy_campaign(store,candidate_id)
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None or candidate.scope is not SCOPE:
        raise ValueError("unknown spot_4h candidate")
    if store.scoped_rule_status(candidate_id) != "queued":
        raise ValueError("only queued spot_4h candidates may replay")
    champion = store.load_active_scoped_rule(SCOPE, symbol=candidate.symbol)
    if candidate.parent_rule_id != (champion.rule_id if champion else "bootstrap"):
        raise ValueError("candidate lineage does not match champion")
    candles = store.list_asset_candles(candidate.symbol, "4h", as_of=now)
    history = spot_4h_history_progress(candles, now=now)
    metrics = _walk_forward(candles, candidate.parameters, first_test_bars=MIN_BARS // 2)
    champion_metrics = (
        _walk_forward(candles, champion.parameters, first_test_bars=MIN_BARS // 2)
        if champion else None
    )
    waiting = list(history["reason_codes"])
    if metrics["closed_trades"] < 6:
        waiting.append("minimum_6_oos_closed_trades")
    rejected = []
    if metrics["net_return_pct"] <= 0:
        rejected.append("nonpositive_net_return")
    if metrics["max_drawdown_pct"] >= 8:
        rejected.append("max_drawdown_at_least_8pct")
    if metrics["hard_risk_violations"]:
        rejected.append("hard_risk_violation")
    if champion_metrics:
        required = (champion_metrics["risk_score"] * .9
                    if champion_metrics["risk_score"] > 0
                    else champion_metrics["risk_score"])
        if metrics["risk_score"] < required:
            rejected.append("risk_score_below_90pct_champion")
        if metrics["expected_shortfall_pct"] < champion_metrics["expected_shortfall_pct"] - .25:
            rejected.append("expected_shortfall_worse_than_champion")
    status = "deferred" if waiting else "reject" if rejected else "pass"
    evaluation = ScopedRuleEvaluation.create(
        candidate_id=candidate_id, symbol=candidate.symbol, scope=SCOPE,
        kind="replay", status=status, evaluated_at=now, started_at=None,
        sample_count=int(metrics["closed_trades"]), coverage=history["coverage"],
        champion_score=float(champion_metrics["risk_score"]) if champion_metrics else 0,
        challenger_score=float(metrics["risk_score"]),
        reason_codes=tuple(waiting or rejected), metrics=metrics,
    )
    store.record_scoped_rule_evaluation(evaluation)
    if status in {"pass", "reject"}:
        store.update_scoped_rule_status(
            candidate_id, expected="queued",
            status="replay_passed" if status == "pass" else "rejected",
        )
    return evaluation


def start_spot_4h_soak(
    store, candidate_id: str, *, evaluation_id: str, now: datetime,
) -> dict:
    require_legacy_campaign(store,candidate_id)
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None or candidate.scope is not SCOPE:
        raise ValueError("unknown spot_4h candidate")
    replay = store.scoped_rule_evaluation(evaluation_id)
    latest = store.latest_scoped_rule_evaluation(candidate_id, kind="replay")
    if (replay is None or latest is None or latest.evaluation_id != evaluation_id
            or replay.candidate_id != candidate_id or replay.status != "pass"
            or replay.kind != "replay"):
        raise ValueError("start-soak requires the exact passing replay evaluation")
    if store.scoped_rule_status(candidate_id) != "replay_passed":
        raise ValueError("candidate is not replay-passed")
    lifecycle = store.asset_lifecycle(candidate.symbol, SCOPE).start_soak()
    registry = store.set_scoped_challenger(candidate_id, now=now)
    store.save_asset_lifecycle(lifecycle, updated_at=now, evaluation_id=evaluation_id)
    return registry


def preview_spot_4h_soak(store, candidate_id: str, *, now: datetime,
                         started_at: datetime | None = None,
                         round_trip_cost_pct: float = ROUND_TRIP_COST_PCT) -> RuleGatePreview:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None or candidate.scope is not SCOPE:
        raise ValueError("unknown spot_4h candidate")
    registry = store.scoped_rule_registry(SCOPE, symbol=candidate.symbol)
    if registry["challenger_id"] != candidate_id:
        raise ValueError("candidate is not active challenger")
    started = started_at or datetime.fromisoformat(registry["updated_at"])
    if started > now:
        raise ValueError("soak start is in the future")
    heartbeats = [row for row in store.list_portfolio_soak_ticks(symbol=candidate.symbol)
                  if row["scope"] == SCOPE.value
                  and started <= datetime.fromisoformat(row["created_at"]) <= now]
    healthy = {int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000) // WIDTH
               for row in heartbeats if row["status"] in {"success", "skipped_no_setup"}}
    expected = max(1, ceil((now - started).total_seconds() / (WIDTH / 1000)))
    coverage = min(1.0, len(healthy) / expected)
    rows = [row for row in store.list_scoped_rule_soak_ticks(candidate_id, as_of=now)
            if started <= datetime.fromisoformat(row["created_at"]) <= now]
    distinct_matured = {}
    for row in rows:
        if row["directional_return_pct"] is None or not row["challenger_allowed"]:
            continue
        if started_at is not None and datetime.fromisoformat(row["signal_timestamp"]) < started:
            continue
        slot = int(datetime.fromisoformat(row["signal_timestamp"]).timestamp()
                   // (WIDTH / 1000))
        distinct_matured.setdefault(slot, row)
    matured = list(distinct_matured.values())
    score = fmean(float(row["directional_return_pct"]) - round_trip_cost_pct
                  for row in matured) if matured else 0.0
    waiting = []
    if now - started < timedelta(days=14):
        waiting.append("minimum_14_days")
    if coverage < .95:
        waiting.append("4h_heartbeat_coverage_below_95pct")
    if len(matured) < 6:
        waiting.append("minimum_6_matured_12h_setups")
    rejected = []
    violations = sum(bool(row["hard_risk_violation"]) for row in heartbeats + rows)
    if violations:
        rejected.append("hard_risk_violation")
    if score <= 0:
        rejected.append("nonpositive_soak_score")
    status = "reject" if "hard_risk_violation" in rejected else (
        "deferred" if waiting else "reject" if rejected else "pass"
    )
    values = dict(
        candidate_id=candidate_id, symbol=candidate.symbol, scope=SCOPE,
        kind="soak", status=status, evaluated_at=now, started_at=started,
        sample_count=len(matured), coverage=coverage,
        champion_score=0, challenger_score=score,
        reason_codes=tuple(rejected or waiting),
        metrics={"heartbeat_slots": len(healthy), "expected_slots": expected,
                 "matured_setups": len(matured)},
    )
    return RuleGatePreview(values, tuple(rejected + waiting), violations)


def evaluate_spot_4h_soak(store, candidate_id: str, *, now: datetime) -> ScopedRuleEvaluation:
    require_legacy_campaign(store,candidate_id)
    evaluation = ScopedRuleEvaluation.create(**preview_spot_4h_soak(store, candidate_id, now=now).values)
    store.record_scoped_rule_evaluation(evaluation)
    if evaluation.status == "reject":
        store.reject_scoped_challenger(candidate_id, now=now)
    return evaluation


def activate_spot_4h_rule(
    store, candidate_id: str, *, evaluation_id: str, now: datetime,
) -> dict:
    require_legacy_campaign(store,candidate_id)
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None or candidate.scope is not SCOPE:
        raise ValueError("unknown spot_4h candidate")
    evaluation = store.scoped_rule_evaluation(evaluation_id)
    latest = store.latest_scoped_rule_evaluation(candidate_id, kind="soak")
    if (evaluation is None or latest is None or latest.evaluation_id != evaluation_id
            or evaluation.candidate_id != candidate_id or evaluation.status != "pass"
            or evaluation.kind != "soak"):
        raise ValueError("activation requires the exact passing soak evaluation")
    registry = store.promote_scoped_challenger(candidate_id, now=now)
    store.save_asset_lifecycle(
        store.asset_lifecycle(candidate.symbol, SCOPE), updated_at=now,
        evaluation_id=evaluation_id,
    )
    return registry
