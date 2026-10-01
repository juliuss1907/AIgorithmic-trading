"""Manual ETH Spot rule lifecycle backed by closed daily candles and soak evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from math import ceil
from statistics import fmean

from intraday.assets import AssetCapability, asset_spec
from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters, SpotRuleProposal
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation
from intraday.spot_signal import BinanceSpotDailyClient, evaluate_donchian


SPOT = DecisionScope.SPOT_DAILY
DAY_MS = 86_400_000
ROUND_TRIP_COST_PCT = 0.30  # 10 bps fee/fill + 5 bps slippage/side.


def _require_eth_spot(symbol: str, scope: DecisionScope) -> str:
    spec = asset_spec(symbol)
    if spec.symbol != "ETHUSDT" or scope is not SPOT or spec.capability is not AssetCapability.FULL:
        raise ValueError("asset rule rollout currently supports ETHUSDT spot_daily only")
    return spec.symbol


def bootstrap_asset_spot_rule(store, symbol: str, *, now: datetime,
                              client: BinanceSpotDailyClient | None = None) -> ScopedRuleCandidate:
    symbol = _require_eth_spot(symbol, SPOT)
    if store.load_active_scoped_rule(SPOT, symbol=symbol) or store.has_open_scoped_rule_candidate(SPOT, symbol=symbol):
        raise ValueError("asset already has a champion or open candidate")
    client = client or BinanceSpotDailyClient(asset_catalog=store.asset_catalog())
    store.record_asset_daily_candles(symbol, client.candles(symbol=symbol, limit=1000, now=now))
    candidate = ScopedRuleCandidate.create(
        rule_id=f"{symbol.lower()}-spot-baseline-v1", parent_rule_id="bootstrap",
        thesis_id="deterministic-baseline", symbol=symbol, scope=SPOT,
        parameters=SpotRuleParameters(), created_at=now,
        model_ref="deterministic/baseline", prompt_version="spot-baseline-v1",
        rationale="Bounded Donchian and ATR baseline for ETH daily Spot replay.",
    )
    store.register_scoped_rule(candidate)
    return candidate


def _coverage(candles: list[list]) -> float:
    if not candles:
        return 0.0
    span = (int(candles[-1][0]) - int(candles[0][0])) // DAY_MS + 1
    return min(1.0, len(candles) / span) if span > 0 else 0.0


def _walk_forward(candles: list[list], parameters: SpotRuleParameters,
                  *, first_test_bars: int = 365) -> dict[str, float | int]:
    if len(candles) < 2:
        return {"net_return_pct": 0.0, "max_drawdown_pct": 0.0,
                "expected_shortfall_pct": 0.0, "closed_trades": 0,
                "hard_risk_violations": 0, "risk_score": 0.0}
    first_test = max(first_test_bars, len(candles) // 2)
    boundaries = [first_test + (len(candles) - first_test) * fold // 3 for fold in range(4)]
    equity, peak, drawdown, trades = 1.0, 1.0, 0.0, 0
    daily_returns: list[float] = []
    for start, stop in zip(boundaries, boundaries[1:]):
        position = False
        entry_price = 0.0
        for index in range(start, stop - 1):
            observation = evaluate_donchian(candles[:index + 1], parameters)
            next_open = float(candles[index + 1][1])
            previous_value = (
                equity * float(candles[index][4]) / entry_price
                if position else equity
            )
            if position and observation.exit:
                exit_price = next_open * (1 - 0.0015)
                equity *= exit_price / entry_price
                position, trades = False, trades + 1
            elif not position and observation.entry:
                entry_price = next_open * (1 + 0.0015)
                position = True
            marked = (
                equity * float(candles[index + 1][4]) / entry_price
                if position else equity
            )
            peak = max(peak, marked)
            drawdown = max(drawdown, (peak - marked) / peak * 100)
            daily_returns.append((marked / previous_value - 1) * 100)
        if position:
            exit_price = float(candles[stop - 1][4]) * (1 - 0.0015)
            previous_equity = equity * float(candles[stop - 1][4]) / entry_price
            equity *= exit_price / entry_price
            trades += 1
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak * 100)
            daily_returns.append((equity / previous_equity - 1) * 100)
    tail = sorted(daily_returns)[:max(1, ceil(len(daily_returns) * 0.05))]
    net = (equity - 1) * 100
    return {"net_return_pct": net, "max_drawdown_pct": drawdown,
            "expected_shortfall_pct": fmean(tail) if tail else 0.0,
            "closed_trades": trades, "hard_risk_violations": 0,
            "risk_score": net - drawdown}


def replay_asset_spot_rule(store, candidate_id: str, *, now: datetime) -> ScopedRuleEvaluation:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None:
        raise ValueError("unknown scoped rule candidate")
    _require_eth_spot(candidate.symbol, candidate.scope)
    if store.scoped_rule_status(candidate_id) != "queued":
        raise ValueError("only a queued candidate may be replayed")
    champion = store.load_active_scoped_rule(SPOT, symbol=candidate.symbol)
    expected_parent = champion.rule_id if champion else "bootstrap"
    if candidate.parent_rule_id != expected_parent:
        raise ValueError("candidate lineage does not match the asset champion")
    candles = store.list_asset_daily_candles(candidate.symbol, as_of=now)
    coverage = _coverage(candles)
    metrics = _walk_forward(candles, candidate.parameters)
    champion_metrics = _walk_forward(candles, champion.parameters) if champion else None
    waiting = []
    if len(candles) < 730:
        waiting.append("minimum_730_closed_candles")
    if coverage < 0.99:
        waiting.append("daily_candle_coverage_below_99pct")
    if candles and now.timestamp() * 1000 - int(candles[-1][6]) > 26 * 3_600_000:
        waiting.append("latest_daily_candle_stale")
    if metrics["closed_trades"] < 6:
        waiting.append("minimum_6_oos_closed_trades")
    rejected = []
    if metrics["net_return_pct"] <= 0:
        rejected.append("nonpositive_net_return")
    if metrics["max_drawdown_pct"] >= 8:
        rejected.append("max_drawdown_at_least_8pct")
    if metrics["hard_risk_violations"]:
        rejected.append("hard_risk_violation")
    if champion_metrics is not None:
        required_score = (
            champion_metrics["risk_score"] * 0.9
            if champion_metrics["risk_score"] > 0 else champion_metrics["risk_score"]
        )
        if metrics["risk_score"] < required_score:
            rejected.append("risk_score_below_90pct_champion")
        if metrics["expected_shortfall_pct"] < champion_metrics["expected_shortfall_pct"] - 0.25:
            rejected.append("expected_shortfall_worse_than_champion")
    status = "deferred" if waiting else "reject" if rejected else "pass"
    evaluation = ScopedRuleEvaluation.create(
        candidate_id=candidate_id, symbol=candidate.symbol, scope=SPOT,
        kind="replay", status=status, evaluated_at=now, started_at=None,
        sample_count=int(metrics["closed_trades"]), coverage=coverage,
        champion_score=float(champion_metrics["risk_score"]) if champion_metrics else 0.0,
        challenger_score=float(metrics["risk_score"]),
        reason_codes=tuple(waiting or rejected), metrics=metrics,
    )
    store.record_scoped_rule_evaluation(evaluation)
    if status == "pass":
        store.update_scoped_rule_status(candidate_id, expected="queued", status="replay_passed")
    elif status == "reject":
        store.update_scoped_rule_status(candidate_id, expected="queued", status="rejected")
    return evaluation


def start_asset_spot_soak(store, candidate_id: str, *, evaluation_id: str,
                          now: datetime) -> dict:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None:
        raise ValueError("unknown scoped rule candidate")
    _require_eth_spot(candidate.symbol, candidate.scope)
    evaluation = store.scoped_rule_evaluation(evaluation_id)
    latest = store.latest_scoped_rule_evaluation(candidate_id, kind="replay")
    if (evaluation is None or latest is None or latest.evaluation_id != evaluation_id
            or evaluation.candidate_id != candidate_id
            or evaluation.symbol != candidate.symbol or evaluation.kind != "replay"
            or evaluation.status != "pass"):
        raise ValueError("start-soak requires the exact passing replay evaluation")
    if store.scoped_rule_status(candidate_id) != "replay_passed":
        raise ValueError("candidate is not replay-passed")
    lifecycle = store.asset_lifecycle(candidate.symbol, SPOT).start_soak()
    registry = store.set_scoped_challenger(candidate_id, now=now)
    store.save_asset_lifecycle(lifecycle, updated_at=now, evaluation_id=evaluation_id)
    return registry


def evaluate_asset_spot_soak(store, candidate_id: str, *, now: datetime) -> ScopedRuleEvaluation:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None:
        raise ValueError("unknown scoped rule candidate")
    _require_eth_spot(candidate.symbol, candidate.scope)
    registry = store.scoped_rule_registry(SPOT, symbol=candidate.symbol)
    if registry["challenger_id"] != candidate_id:
        raise ValueError("candidate is not the active asset challenger")
    started = datetime.fromisoformat(registry["updated_at"])
    heartbeats = [row for row in store.list_portfolio_soak_ticks(symbol=candidate.symbol)
                  if row["scope"] == SPOT.value
                  and started <= datetime.fromisoformat(row["created_at"]) <= now]
    days = {(datetime.fromisoformat(row["created_at"]).date()) for row in heartbeats
            if row["status"] in {"success", "skipped_no_setup"}}
    expected = max(1, ceil((now - started).total_seconds() / 86_400))
    coverage = min(1.0, len(days) / expected)
    rows = [row for row in store.list_scoped_rule_soak_ticks(candidate_id, as_of=now)
            if started <= datetime.fromisoformat(row["created_at"]) <= now]
    matured = [row for row in rows if row["directional_return_pct"] is not None
               and row["challenger_allowed"]]
    score = fmean(float(row["directional_return_pct"]) - ROUND_TRIP_COST_PCT
                  for row in matured) if matured else 0.0
    champion = store.load_active_scoped_rule(SPOT, symbol=candidate.symbol)
    champion_rows = [float(row["directional_return_pct"]) - ROUND_TRIP_COST_PCT
                     for row in rows if row["directional_return_pct"] is not None
                     and row["champion_allowed"]]
    champion_score = fmean(champion_rows) if champion_rows else 0.0
    waiting = []
    if now - started < timedelta(days=30):
        waiting.append("minimum_30_days")
    if coverage < 0.95:
        waiting.append("daily_heartbeat_coverage_below_95pct")
    if len(matured) < 3:
        waiting.append("minimum_3_matured_setups")
    rejected = []
    if any(row["hard_risk_violation"] for row in heartbeats + rows):
        rejected.append("hard_risk_violation")
    if champion is None and score <= 0:
        rejected.append("nonpositive_soak_score")
    if champion is not None and score < champion_score - 0.05:
        rejected.append("soak_underperformance")
    status = "reject" if "hard_risk_violation" in rejected else "deferred" if waiting else "reject" if rejected else "pass"
    evaluation = ScopedRuleEvaluation.create(
        candidate_id=candidate_id, symbol=candidate.symbol, scope=SPOT,
        kind="soak", status=status, evaluated_at=now, started_at=started,
        sample_count=len(matured), coverage=coverage,
        champion_score=champion_score, challenger_score=score,
        reason_codes=tuple(rejected or waiting),
        metrics={"heartbeat_days": len(days), "expected_days": expected,
                 "matured_setups": len(matured)},
    )
    store.record_scoped_rule_evaluation(evaluation)
    return evaluation


def activate_asset_spot_rule(store, candidate_id: str, *, evaluation_id: str,
                             now: datetime) -> dict:
    candidate = store.load_scoped_rule(candidate_id)
    if candidate is None:
        raise ValueError("unknown scoped rule candidate")
    _require_eth_spot(candidate.symbol, candidate.scope)
    evaluation = store.scoped_rule_evaluation(evaluation_id)
    latest = store.latest_scoped_rule_evaluation(candidate_id, kind="soak")
    if (evaluation is None or latest is None or latest.evaluation_id != evaluation_id
            or evaluation.candidate_id != candidate_id
            or evaluation.symbol != candidate.symbol or evaluation.kind != "soak"
            or evaluation.status != "pass"):
        raise ValueError("activation requires the exact passing soak evaluation")
    registry = store.promote_scoped_challenger(candidate_id, now=now)
    store.save_asset_lifecycle(
        store.asset_lifecycle(candidate.symbol, SPOT),
        updated_at=now, evaluation_id=evaluation_id,
    )
    return registry


def propose_asset_spot_rule(store, symbol: str, *, client, now: datetime) -> ScopedRuleCandidate:
    """One operator-triggered bounded proposal; no scheduler path calls this."""
    symbol = _require_eth_spot(symbol, SPOT)
    champion = store.load_active_scoped_rule(SPOT, symbol=symbol)
    if champion is None:
        raise ValueError("asset needs an active champion before an LLM challenger")
    if store.has_open_scoped_rule_candidate(SPOT, symbol=symbol):
        raise ValueError("asset already has an open candidate")
    latest_replay = store.latest_scoped_rule_evaluation(champion.rule_id, kind="replay")
    latest_soak = store.latest_scoped_rule_evaluation(champion.rule_id, kind="soak")
    snapshot = store.latest_snapshot(market="binance_spot", symbol=symbol)
    if snapshot is None:
        raise ValueError("asset needs a Spot market snapshot")
    thesis = store.latest_market_thesis_bundle()
    proposal = client.complete(
        workflow="asset_spot_rule_generator", response_model=SpotRuleProposal,
        system_prompt=(
            "Propose only bounded Donchian/ATR parameters for this symbol's long-only "
            "daily Spot rule. Hard portfolio risk and deterministic exits are immutable."
        ),
        input_payload={
            "symbol": symbol,
            "champion": champion.model_dump(mode="json"),
            "spot_market": snapshot.model_dump(mode="json"),
            "replay": latest_replay.model_dump(mode="json") if latest_replay else None,
            "soak": latest_soak.model_dump(mode="json") if latest_soak else None,
            "market_thesis": thesis.model_dump(mode="json") if thesis else None,
            "global_context": [item.model_dump(mode="json") for item in
                               store.list_external_observations(source="cryptorank", limit=1)],
        },
        now=now,
    )
    prior = [store.load_scoped_rule(row["id"]) for row in store.list_scoped_rules(SPOT, symbol=symbol)]
    if any(item.parameters == proposal.parameters for item in prior):
        raise ValueError("proposed parameters duplicate an existing asset rule")
    identity = hashlib.sha256(json.dumps({
        "symbol": symbol, "scope": SPOT.value, "parent": champion.rule_id,
        "parameters": proposal.parameters.model_dump(mode="json"),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    candidate = ScopedRuleCandidate.create(
        rule_id=f"{symbol.lower()}-spot-candidate-{identity}",
        parent_rule_id=champion.rule_id,
        thesis_id=thesis.thesis_id if thesis else "operator-proposal",
        symbol=symbol, scope=SPOT, parameters=proposal.parameters,
        created_at=now, model_ref=client.model_ref,
        prompt_version="asset-spot-proposal-v1", rationale=proposal.rationale,
    )
    store.register_scoped_rule(candidate)
    return candidate
