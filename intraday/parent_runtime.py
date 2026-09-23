"""Combined parent paper cycle with deterministic exits ahead of AI entries."""

from __future__ import annotations

from datetime import datetime

from intraday.contracts import (
    DecisionScope,
    FeatureSnapshot,
    PerpRuleParameters,
    SpotRuleParameters,
)
from intraday.parent_paper import apply_paper_target
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.scoped_gate import ScopedEntryGate
from intraday.spot_signal import DonchianObservation


def _mark_state(
    state: ParentPortfolioState, snapshot: FeatureSnapshot, now: datetime
) -> ParentPortfolioState:
    payload = state.model_dump()
    payload.update(
        {
            "mark_price": float(snapshot.features["mark_price"]),
            "updated_at": now,
        }
    )
    marked = ParentPortfolioState.model_validate(payload)
    if state.updated_at.date() != now.date():
        marked = marked.model_copy(update={"day_start_equity": marked.equity})
    return marked.model_copy(
        update={"high_water_mark": max(state.high_water_mark, marked.equity)}
    )


def run_parent_paper_cycle(
    store,
    provider,
    snapshot: FeatureSnapshot,
    spot_observation: DonchianObservation,
    *,
    spot_rule: SpotRuleParameters,
    perp_rule: PerpRuleParameters,
    now: datetime,
) -> dict:
    store.record_snapshot(snapshot)
    state = store.load_parent_portfolio_state()
    if state is None:
        raise ValueError("parent paper portfolio is not initialized")
    state = _mark_state(state, snapshot, now)
    gate = ScopedEntryGate()
    fills = []
    provider_errors = []

    def execute(authorization):
        nonlocal state
        state, fill = apply_paper_target(
            state,
            authorization,
            bid=snapshot.bid,
            ask=snapshot.ask,
            now=now,
        )
        if fill is not None:
            store.record_parent_paper_fill(fill)
            fills.append(fill)

    # Hard exits always run before any provider call.
    if state.drawdown <= -gate.coordinator.policy.max_drawdown_pct:
        if state.spot_quantity:
            execute(gate.deterministic_exit(
                state, DecisionScope.SPOT_DAILY, "parent_drawdown_limit"
            ))
        if state.perp_quantity:
            execute(gate.deterministic_exit(
                state, DecisionScope.PERP_INTRADAY, "parent_drawdown_limit"
            ))
        state = state.model_copy(
            update={"entries_paused": True, "halt_reason": "parent_drawdown_limit"}
        )
    else:
        if state.spot_quantity and spot_observation.exit:
            execute(gate.deterministic_exit(
                state, DecisionScope.SPOT_DAILY, "donchian_exit"
            ))
        if state.perp_quantity and state.perp_entry_price is not None:
            signed_return = (
                (state.mark_price / state.perp_entry_price - 1)
                * (1 if state.perp_quantity > 0 else -1)
            )
            if signed_return <= -perp_rule.stop_distance_pct:
                execute(gate.deterministic_exit(
                    state, DecisionScope.PERP_INTRADAY, "perp_stop_loss"
                ))

    if state.paper_active:
        if state.spot_quantity == 0 and spot_observation.entry:
            try:
                scoped = provider.decide_scoped(
                    snapshot,
                    f"{snapshot.symbol}:spot_daily:{int(now.timestamp() * 1000)}",
                    DecisionScope.SPOT_DAILY,
                    now,
                )
            except RuntimeError:
                provider_errors.append(DecisionScope.SPOT_DAILY.value)
            else:
                authorization = gate.spot_entry(
                    state,
                    scoped.decision,
                    spot_rule,
                    donchian_entry=True,
                    size_multiplier=spot_observation.size_multiplier,
                )
                if authorization.allowed:
                    execute(authorization)
        try:
            scoped = provider.decide_scoped(
                snapshot,
                f"{snapshot.symbol}:perp_intraday:{int(now.timestamp() * 1000)}",
                DecisionScope.PERP_INTRADAY,
                now,
            )
        except RuntimeError:
            provider_errors.append(DecisionScope.PERP_INTRADAY.value)
        else:
            authorization = gate.perp_entry(state, scoped.decision, perp_rule)
            if authorization.allowed:
                execute(authorization)

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
