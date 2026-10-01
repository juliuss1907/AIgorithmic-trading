"""Budgeted, evidence-triggered per-asset rule proposals; no auto-promotion."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json

from intraday.asset_rule_lifecycle import _walk_forward
from intraday.assets import asset_spec
from intraday.contracts import (
    DecisionScope, PerpRuleProposal, ScopedRuleCandidate, SpotRuleProposal,
)
from intraday.perp_bootstrap_lifecycle import _eligible_returns


def _spot_evidence(store, symbol: str, *, since: datetime, now: datetime) -> bool:
    candles = store.list_asset_candles(symbol, "4h", as_of=now)
    return sum(
        datetime.fromtimestamp(int(row[6]) / 1000, now.tzinfo) > since
        for row in candles
    ) >= 3


def _perp_evidence(store, symbol: str, *, since: datetime, now: datetime) -> bool:
    _, summary = store.scoped_rule_replay_evidence(
        DecisionScope.PERP_INTRADAY, symbol=symbol, after=since, before=now,
    )
    return summary["outcomes"] >= 100 and summary["coverage"] >= .95


def _champion_deteriorated(store, champion, *, now: datetime) -> bool:
    if champion.scope is DecisionScope.SPOT_4H:
        candles = store.list_asset_candles(champion.symbol, "4h", as_of=now)
        if len(candles) < 365 * 6:
            return False
        metrics = _walk_forward(candles[-365 * 6:], champion.parameters,
                                first_test_bars=365 * 3)
        replay = store.latest_scoped_rule_evaluation(champion.rule_id, kind="replay")
        baseline = replay.challenger_score if replay else 0
        return (metrics["net_return_pct"] <= 0 or
                (baseline > 0 and metrics["risk_score"] < baseline * .75))
    if champion.scope is DecisionScope.PERP_INTRADAY:
        rows, summary = store.scoped_rule_replay_evidence(
            champion.scope, symbol=champion.symbol,
            after=now - timedelta(days=14), before=now,
        )
        scores = _eligible_returns(champion, rows)
        return (summary["outcomes"] >= 100 and summary["coverage"] >= .95
                and bool(scores) and sum(scores) / len(scores) <= 0)
    return False


def auto_propose_asset_rule(
    store, symbol: str, scope: DecisionScope, *, client, now: datetime,
) -> ScopedRuleCandidate | None:
    """One bounded proposal on fresh evidence; never touches registry promotion."""
    symbol = store.asset_spec(symbol).symbol
    if scope not in {DecisionScope.SPOT_4H, DecisionScope.PERP_INTRADAY}:
        raise ValueError("auto proposals support spot_4h and perp_intraday only")
    if store.has_open_scoped_rule_candidate(scope, symbol=symbol):
        return None
    workflow = f"asset_{scope.value}_rule_generator_{symbol}"
    calls, latest_call = store.asset_proposal_call_window(
        workflow, since=now - timedelta(days=90),
    )
    if calls >= 3:
        return None
    champion = store.load_active_scoped_rule(scope, symbol=symbol)
    rules = store.list_scoped_rules(scope, symbol=symbol)
    rejected = next((row for row in rules if row["status"] == "rejected"), None)
    rejected_rule = store.load_scoped_rule(rejected["id"]) if rejected else None
    rejected_evaluation = None
    if rejected_rule:
        rejected_evaluation = (
            store.latest_scoped_rule_evaluation(rejected_rule.rule_id, kind="soak")
            or store.latest_scoped_rule_evaluation(rejected_rule.rule_id, kind="replay")
        )
    rejection_trigger = bool(
        rejected_rule and rejected_evaluation and rejected_evaluation.status == "reject"
    )
    deterioration_trigger = bool(
        champion and _champion_deteriorated(store, champion, now=now)
    )
    if not (rejection_trigger or deterioration_trigger):
        return None
    parent = champion or rejected_rule
    if parent is None:
        return None
    boundary = max(
        (value for value in (
            latest_call,
            rejected_evaluation.evaluated_at if rejection_trigger else None,
        ) if value is not None),
        default=parent.created_at,
    )
    if scope is DecisionScope.SPOT_4H:
        if not _spot_evidence(store, symbol, since=boundary, now=now):
            return None
        snapshot = next((item for item in reversed(store.list_snapshots(
            limit=100, market="binance_spot", symbol=symbol,
        )) if item.timeframe == "4h" and item.feature_schema_version == "3"), None)
        if snapshot is None or not all(snapshot.freshness.get(f"candles_{interval}")
                                       for interval in ("4h", "8h", "1d")):
            return None
        response_model = SpotRuleProposal
        prompt = (
            "Propose only entry_window and exit_window for this long-only UTC 4h "
            "Donchian rule. Keep ATR period, Jev filters, hard risk and exits unchanged. "
            "Native 8h/1d are context, never independent triggers."
        )
    else:
        if not _perp_evidence(store, symbol, since=boundary, now=now):
            return None
        snapshot = store.latest_snapshot(market="binance_usdm_perp", symbol=symbol)
        if snapshot is None:
            return None
        response_model = PerpRuleProposal
        prompt = (
            "Propose bounded Perp entry filters only for this symbol. Preserve "
            "hard portfolio risk, leverage, liquidation safety and deterministic exits."
        )
    thesis = store.latest_market_thesis_bundle()
    proposal = client.complete(
        workflow=workflow, response_model=response_model, system_prompt=prompt,
        input_payload={
            "symbol": symbol, "scope": scope.value,
            "parent": parent.model_dump(mode="json"),
            "trigger": "rejected_evaluation" if rejection_trigger else "champion_deterioration",
            "evaluation": (rejected_evaluation.model_dump(mode="json")
                           if rejection_trigger else None),
            "snapshot": snapshot.model_dump(mode="json"),
            "market_thesis": thesis.model_dump(mode="json") if thesis else None,
        },
        now=now,
    )
    if scope is DecisionScope.SPOT_4H:
        base = parent.parameters
        proposed = proposal.parameters
        if (proposed.atr_period != base.atr_period or
                proposed.jev_confidence_threshold != base.jev_confidence_threshold or
                proposed.allowed_regimes != base.allowed_regimes):
            raise ValueError("initial spot_4h proposal may tune Donchian windows only")
    for row in rules:
        old = store.load_scoped_rule(row["id"])
        if old.parameters == proposal.parameters:
            return None
    parent_id = champion.rule_id if champion else "bootstrap"
    identity = hashlib.sha256(json.dumps({
        "symbol": symbol, "scope": scope.value, "parent": parent_id,
        "parameters": proposal.parameters.model_dump(mode="json"),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    candidate = ScopedRuleCandidate.create(
        rule_id=f"{symbol.lower()}-{scope.value}-candidate-{identity}",
        parent_rule_id=parent_id,
        thesis_id=thesis.thesis_id if thesis else "auto-proposal",
        scope=scope, symbol=symbol, parameters=proposal.parameters,
        created_at=now, model_ref=client.model_ref,
        prompt_version="asset-auto-proposal-v1", rationale=proposal.rationale,
    )
    store.register_scoped_rule(candidate)
    return candidate
