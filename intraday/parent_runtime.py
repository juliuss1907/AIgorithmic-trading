"""Combined parent paper cycle with deterministic exits ahead of AI entries."""

from __future__ import annotations

from datetime import datetime

from intraday.contracts import (
    DecisionScope,
    Direction,
    FeatureSnapshot,
    PerpRuleParameters,
    ScopedRuleCandidate,
    SpotRuleParameters,
)
from intraday.journal import record_scoped_signal
from intraday.parent_paper import apply_paper_target
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.scoped_gate import ScopedEntryGate
from intraday.scoped_rule_lifecycle import rule_allows_answers
from intraday.spot_signal import DonchianObservation


def _rule_parts(rule, fallback_id: str):
    if isinstance(rule, ScopedRuleCandidate):
        return rule.parameters, rule.rule_id
    return rule, fallback_id


def _mark_state(
    state: ParentPortfolioState,
    perp_snapshot: FeatureSnapshot,
    spot_snapshot: FeatureSnapshot,
    now: datetime,
) -> ParentPortfolioState:
    perp_reference = float(
        perp_snapshot.features.get("reference_price")
        or perp_snapshot.features.get("mark_price")
        or perp_snapshot.features["price"]
    )
    spot_reference = float(
        spot_snapshot.features.get("reference_price")
        or spot_snapshot.features["price"]
    )
    payload = state.model_dump()
    payload.update(
        {
            "mark_price": perp_reference,
            "spot_price": spot_reference,
            "perp_mark_price": perp_reference,
            "updated_at": now,
        }
    )
    marked = ParentPortfolioState.model_validate(payload)
    if state.updated_at.date() != now.date():
        marked = marked.model_copy(update={"day_start_equity": marked.equity})
    return marked.model_copy(
        update={"high_water_mark": max(state.high_water_mark, marked.equity)}
    )


def flatten_parent_paper_positions(
    store,
    state: ParentPortfolioState,
    *,
    now: datetime,
    bid: float | None = None,
    ask: float | None = None,
    actor: str,
) -> ParentPortfolioState:
    """Flatten both paper sleeves through fills so open trades remain auditable."""
    bid = state.mark_price if bid is None else bid
    ask = state.mark_price if ask is None else ask
    gate = ScopedEntryGate()
    current = state
    for scope, quantity in (
        (DecisionScope.SPOT_DAILY, current.spot_quantity),
        (DecisionScope.PERP_INTRADAY, current.perp_quantity),
    ):
        if not quantity:
            continue
        authorization = gate.deterministic_exit(current, scope, "manual")
        current, fill = apply_paper_target(
            current, authorization, bid=bid, ask=ask, now=now
        )
        if fill is not None:
            store.record_parent_paper_fill(fill, close_reason="manual")
    current = current.model_copy(
        update={
            "entries_paused": True,
            "halt_reason": "operator_flatten",
            "updated_at": now,
        }
    )
    store.save_parent_portfolio_state(
        current, event_kind="flatten", actor=actor
    )
    return current


def run_parent_paper_cycle(
    store,
    provider,
    snapshot: FeatureSnapshot,
    spot_observation: DonchianObservation,
    *,
    spot_snapshot: FeatureSnapshot | None = None,
    spot_rule: SpotRuleParameters | ScopedRuleCandidate,
    perp_rule: PerpRuleParameters | ScopedRuleCandidate,
    experiment_pair_ids: dict[DecisionScope, str] | None = None,
    decision_scopes: tuple[DecisionScope, ...] = (
        DecisionScope.SPOT_DAILY,
        DecisionScope.PERP_INTRADAY,
    ),
    record_snapshot: bool = True,
    now: datetime,
) -> dict:
    if spot_snapshot is None:
        if snapshot.feature_schema_version == "2":
            raise ValueError("feature schema v2 requires a Binance Spot snapshot")
        spot_snapshot = snapshot
    if snapshot.feature_schema_version == "2" and snapshot.market != "binance_usdm_perp":
        raise ValueError("perp runtime snapshot must come from Binance USD-M")
    if spot_snapshot.feature_schema_version == "2" and spot_snapshot.market != "binance_spot":
        raise ValueError("spot runtime snapshot must come from Binance Spot")
    scope_snapshots = {
        DecisionScope.SPOT_DAILY: spot_snapshot,
        DecisionScope.PERP_INTRADAY: snapshot,
    }
    if record_snapshot:
        store.record_snapshot(snapshot)
        if spot_snapshot.snapshot_id != snapshot.snapshot_id:
            store.record_snapshot(spot_snapshot)
    state = store.load_parent_portfolio_state()
    if state is None:
        raise ValueError("parent paper portfolio is not initialized")
    state = _mark_state(state, snapshot, spot_snapshot, now)
    gate = ScopedEntryGate()
    spot_parameters, spot_rule_id = _rule_parts(spot_rule, "spot_rule_parameters")
    perp_parameters, perp_rule_id = _rule_parts(perp_rule, "perp_rule_parameters")
    fills = []
    provider_errors = []
    experiment_pair_ids = experiment_pair_ids or {}

    def decide(scope: DecisionScope):
        market = scope_snapshots[scope]
        tick_id = f"{market.symbol}:{scope.value}:{int(now.timestamp() * 1000)}"
        pair_id = experiment_pair_ids.get(scope)
        if pair_id is None:
            return provider.decide_scoped(market, tick_id, scope, now)
        return provider.decide_scoped(
            market,
            tick_id,
            scope,
            now,
            experiment_pair_id=pair_id,
        )

    def record_challenger_tick(scope, signal_id, scoped):
        champion = store.load_active_scoped_rule(scope)
        challenger = store.load_scoped_challenger(scope)
        if champion is None or challenger is None:
            return
        answers = scoped.trace.jev_answers if scoped.trace is not None else {}
        try:
            champion_allowed = rule_allows_answers(champion, answers)
            challenger_allowed = rule_allows_answers(challenger, answers)
        except (KeyError, TypeError, ValueError):
            champion_allowed = challenger_allowed = False
        store.record_scoped_rule_soak_tick(
            candidate_id=challenger.rule_id,
            signal_id=signal_id,
            champion_allowed=champion_allowed,
            challenger_allowed=challenger_allowed,
            champion_score=0,
            challenger_score=0,
            created_at=now,
        )

    def execute(
        authorization,
        *,
        signal_id=None,
        direction=None,
        stop_distance_pct=None,
        close_reason=None,
    ):
        nonlocal state
        market = scope_snapshots[authorization.scope]
        state, fill = apply_paper_target(
            state, authorization, bid=market.bid, ask=market.ask, now=now,
        )
        if fill is not None:
            stop_loss = None
            if signal_id is not None and stop_distance_pct is not None:
                long_entry = direction in {Direction.BUY, Direction.STRONG_BUY}
                stop_loss = fill.price * (
                    1 - stop_distance_pct if long_entry else 1 + stop_distance_pct
                )
            store.record_parent_paper_fill(
                fill,
                entry_signal_id=signal_id,
                direction=direction.value if direction is not None else None,
                stop_loss=stop_loss,
                close_reason=close_reason,
            )
            fills.append(fill)

    # Hard exits always run before any provider call.
    if state.drawdown <= -gate.coordinator.policy.max_drawdown_pct:
        if state.spot_quantity:
            execute(
                gate.deterministic_exit(
                    state, DecisionScope.SPOT_DAILY, "parent_drawdown_limit"
                ),
                close_reason="parent_drawdown_limit",
            )
        if state.perp_quantity:
            execute(
                gate.deterministic_exit(
                    state, DecisionScope.PERP_INTRADAY, "parent_drawdown_limit"
                ),
                close_reason="parent_drawdown_limit",
            )
        state = state.model_copy(
            update={"entries_paused": True, "halt_reason": "parent_drawdown_limit"}
        )
    else:
        if state.spot_quantity and spot_observation.exit:
            execute(
                gate.deterministic_exit(
                    state, DecisionScope.SPOT_DAILY, "donchian_exit"
                ),
                close_reason="donchian_exit",
            )
        if state.perp_quantity and state.perp_entry_price is not None:
            signed_return = (
                (state.perp_mark_price / state.perp_entry_price - 1)
                * (1 if state.perp_quantity > 0 else -1)
            )
            if signed_return <= -perp_parameters.stop_distance_pct:
                execute(
                    gate.deterministic_exit(
                        state, DecisionScope.PERP_INTRADAY, "perp_stop_loss"
                    ),
                    close_reason="stop_loss",
                )

    if state.paper_active:
        if (
            DecisionScope.SPOT_DAILY in decision_scopes
            and state.spot_quantity == 0
            and spot_observation.entry
        ):
            try:
                scoped = decide(DecisionScope.SPOT_DAILY)
            except RuntimeError:
                provider_errors.append(DecisionScope.SPOT_DAILY.value)
            else:
                authorization = gate.spot_entry(
                    state,
                    scoped.decision,
                    spot_parameters,
                    donchian_entry=True,
                    size_multiplier=spot_observation.size_multiplier,
                )
                signal_id = record_scoped_signal(
                    store,
                    spot_snapshot,
                    scoped,
                    gate_passed=authorization.allowed,
                    gate_reason=(
                        None
                        if authorization.allowed
                        else ";".join(authorization.reason_codes)
                    ),
                    rule_id=spot_rule_id,
                )
                record_challenger_tick(DecisionScope.SPOT_DAILY, signal_id, scoped)
                if authorization.allowed:
                    execute(
                        authorization,
                        signal_id=signal_id,
                        direction=scoped.decision.direction,
                    )
        if DecisionScope.PERP_INTRADAY in decision_scopes:
            try:
                scoped = decide(DecisionScope.PERP_INTRADAY)
            except RuntimeError:
                provider_errors.append(DecisionScope.PERP_INTRADAY.value)
            else:
                authorization = gate.perp_entry(
                    state, scoped.decision, perp_parameters
                )
                signal_id = record_scoped_signal(
                    store,
                    snapshot,
                    scoped,
                    gate_passed=authorization.allowed,
                    gate_reason=(
                        None
                        if authorization.allowed
                        else ";".join(authorization.reason_codes)
                    ),
                    rule_id=perp_rule_id,
                )
                record_challenger_tick(DecisionScope.PERP_INTRADAY, signal_id, scoped)
                if authorization.allowed:
                    reducing = authorization.reduce_only
                    reason = None
                    if reducing:
                        reason = (
                            "take_profit"
                            if scoped.decision.direction == Direction.TAKE_PROFIT
                            else (
                                "no_same_tick_flip"
                                if "no_same_tick_flip" in authorization.reason_codes
                                else "model_exit"
                            )
                        )
                    execute(
                        authorization,
                        signal_id=None if reducing else signal_id,
                        direction=None if reducing else scoped.decision.direction,
                        stop_distance_pct=(
                            None if reducing else perp_parameters.stop_distance_pct
                        ),
                        close_reason=reason,
                    )

    state = state.model_copy(update={"updated_at": now})
    store.save_parent_portfolio_state(
        state, event_kind="paper_cycle", actor="worker"
    )
    return {
        "status": "ok" if state.paper_active else "inactive",
        "fills": len(fills),
        "fill_ids": [fill.fill_id for fill in fills],
        "provider_errors": provider_errors,
        "equity": state.equity,
        "spot_notional": state.spot_notional,
        "perp_notional": state.perp_notional,
        "entries_paused": state.entries_paused,
    }


def run_parent_risk_cycle(
    store,
    snapshot: FeatureSnapshot,
    spot_observation: DonchianObservation,
    *,
    spot_snapshot: FeatureSnapshot | None = None,
    spot_rule: SpotRuleParameters | ScopedRuleCandidate,
    perp_rule: PerpRuleParameters | ScopedRuleCandidate,
    now: datetime,
) -> dict:
    """Run marks and deterministic exits without crossing the provider boundary."""
    return run_parent_paper_cycle(
        store,
        None,
        snapshot,
        spot_observation,
        spot_snapshot=spot_snapshot,
        spot_rule=spot_rule,
        perp_rule=perp_rule,
        decision_scopes=(),
        record_snapshot=False,
        now=now,
    )
