"""Redacted, read-only projections for the external trading operator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from intraday.contracts import DecisionScope
from intraday.decision_evaluation import EVALUATION_HORIZONS
from intraday.portfolio_coordinator import ParentPortfolioPolicy
from intraday.store import IntradayStore


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _market_health(store: IntradayStore, now: datetime) -> dict:
    snapshots = store.list_snapshots(limit=1)
    if not snapshots:
        return {"status": "unknown", "last_event_at": None, "age_seconds": None}
    last = snapshots[-1].event_time.astimezone(timezone.utc)
    age = max(0.0, (now.astimezone(timezone.utc) - last).total_seconds())
    return {
        "status": "ok" if age <= 120 else "stale",
        "last_event_at": last.isoformat(),
        "age_seconds": age,
    }


def build_operator_snapshot(
    store: IntradayStore,
    *,
    now: datetime,
    actions_enabled: bool,
    approval_ttl_seconds: int,
) -> dict:
    parent = store.load_parent_portfolio_state()
    policy = ParentPortfolioPolicy()
    portfolio = None
    risk = {
        "entries_paused": True,
        "halt_reason": "portfolio_not_initialized",
        "daily_return": None,
        "drawdown": None,
        "daily_loss_limit_pct": policy.daily_loss_limit_pct,
        "max_drawdown_pct": policy.max_drawdown_pct,
        "gross_exposure_limit_pct": policy.max_gross_exposure_pct,
        "net_delta_limit_pct": policy.max_abs_net_delta_pct,
        "isolated_margin_limit_pct": policy.max_isolated_margin_pct,
        "leverage": policy.leverage,
    }
    if parent is not None:
        portfolio = {
            "equity": parent.equity,
            "initial_equity": parent.initial_equity,
            "realized_pnl": parent.realized_pnl,
            "fees": parent.fees,
            "funding": parent.funding,
            "mark_price": parent.mark_price,
            "spot_notional": parent.spot_notional,
            "perp_notional": parent.perp_notional,
            "paper_active": parent.paper_active,
            "updated_at": _utc(parent.updated_at),
        }
        risk.update(
            {
                "entries_paused": parent.entries_paused,
                "halt_reason": parent.halt_reason,
                "daily_return": parent.daily_return,
                "drawdown": parent.drawdown,
            }
        )

    thesis = store.latest_market_thesis_bundle()
    retrospective = store.latest_retrospective()
    day_start = now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    experiments = {}
    rules = {}
    for scope in DecisionScope:
        evaluation = store.latest_decision_experiment_evaluation(scope)
        experiments[scope.value] = {
            "pairs": store.decision_experiment_pair_summary(scope),
            "evaluation": (
                {
                    "status": evaluation.status,
                    "evaluated_at": _utc(evaluation.evaluated_at),
                    "evaluation_id": evaluation.evaluation_id,
                }
                if evaluation
                else None
            ),
            "outcomes": store.signal_outcome_summary(
                scope=scope, horizon_sec=EVALUATION_HORIZONS[scope]
            ),
        }
        rules[scope.value] = store.scoped_rule_registry(scope)

    thesis_payload = None
    if thesis is not None:
        thesis_payload = {
            "thesis_id": thesis.thesis_id,
            "generated_at": _utc(thesis.generated_at),
            "intraday": thesis.intraday.model_dump(mode="json"),
            "daily_swing": thesis.daily_swing.model_dump(mode="json"),
        }

    return {
        "schema_version": "1",
        "generated_at": _utc(now),
        "mode": "paper",
        "portfolio": portfolio,
        "risk": risk,
        "health": {
            "database_schema_version": store.schema_version(),
            "market_data": _market_health(store, now),
            "scheduler": store.latest_scheduler_runs(),
        },
        "analysis": {
            "thesis": thesis_payload,
            "latest_retrospective": retrospective,
        },
        "rules": rules,
        "experiments": experiments,
        "signals_24h": store.signal_gate_summary(since=now - timedelta(hours=24)),
        "costs": {"model_cost_today_usd": store.model_cost_since(day_start)},
        "controls": {
            "actions_enabled": actions_enabled,
            "allowed_actions": ["pause", "resume"],
            "approval_ttl_seconds": approval_ttl_seconds,
        },
    }
