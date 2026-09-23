import json
import sys
from datetime import datetime, timedelta, timezone

from intraday.__main__ import main
from intraday.contracts import (
    DecisionScope,
    Direction,
    FeatureSnapshot,
    JevDecision,
    Regime,
    RiskLevel,
    ScopedJevDecision,
)
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.portfolio_soak import evaluate_portfolio_soak, run_soak_cycle
from intraday.store import IntradayStore


START = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


def records():
    result = []
    for index in range(100):
        result.append(
            {
                "scope": DecisionScope.PERP_INTRADAY.value,
                "status": "success",
                "hard_risk_violation": False,
                "created_at": (START + timedelta(minutes=44 * index)).isoformat(),
            }
        )
    for index in range(4):
        result.append(
            {
                "scope": DecisionScope.SPOT_DAILY.value,
                "status": "skipped_no_setup",
                "hard_risk_violation": False,
                "created_at": (START + timedelta(hours=24 * index)).isoformat(),
            }
        )
    return result


def snapshot():
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=START,
        built_at=START,
        bid=99_990,
        ask=100_010,
        features={"price": 100_000, "mark_price": 100_000},
        freshness={"candles": True, "book": True},
    )


def test_soak_requires_72_hours_both_scopes_and_95_percent_availability():
    passed = evaluate_portfolio_soak(records(), evaluated_at=START + timedelta(hours=73))
    short = evaluate_portfolio_soak(records()[:20], evaluated_at=START + timedelta(hours=10))
    failed_records = records()
    for item in failed_records[:10]:
        item["status"] = "provider_error"
    failed = evaluate_portfolio_soak(
        failed_records, evaluated_at=START + timedelta(hours=73)
    )

    assert passed.status == "pass"
    assert short.status == "deferred"
    assert "minimum_72_hours" in short.reason_codes
    assert failed.status == "reject"
    assert "perp_intraday_availability_below_95pct" in failed.reason_codes


def test_soak_cycle_exercises_both_scoped_jev_workflows_without_trading(tmp_path):
    class Provider:
        def __init__(self):
            self.scopes = []

        def decide_scoped(self, market, tick_id, scope, now):
            self.scopes.append(scope)
            decision = JevDecision(
                decision_id=f"decision-{scope.value}",
                tick_id=tick_id,
                snapshot_id=market.snapshot_id,
                direction=Direction.BUY,
                direction_confidence=0.91,
                regime=Regime.TRENDING_UP,
                toxic_flow=0.1,
                entry_quality=4,
                risk_level=RiskLevel.LOW,
                model_ref="fake/jev",
                created_at=now,
            )
            workflow = (
                "spot_daily_entry"
                if scope == DecisionScope.SPOT_DAILY
                else "perp_intraday_entry"
            )
            return ScopedJevDecision(
                scope=scope, workflow=workflow, decision=decision
            )

    store = IntradayStore(tmp_path / "intraday.sqlite")
    provider = Provider()

    result = run_soak_cycle(store, provider, snapshot(), now=START)

    assert result == {"spot_daily": "success", "perp_intraday": "success"}
    assert provider.scopes == [
        DecisionScope.SPOT_DAILY,
        DecisionScope.PERP_INTRADAY,
    ]
    assert len(store.list_portfolio_soak_ticks()) == 2
    assert store.load_parent_portfolio_state() is None


def test_cli_evaluates_soak_then_requires_exact_pass_id_for_activation(
    monkeypatch, capsys, tmp_path
):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    for item in records():
        store.record_portfolio_soak_tick(
            scope=DecisionScope(item["scope"]),
            status=item["status"],
            created_at=datetime.fromisoformat(item["created_at"]),
        )
    inactive = ParentPortfolioState(
        mark_price=100_000,
        day_start_equity=10_000,
        high_water_mark=10_000,
        entries_paused=True,
        paper_active=False,
        updated_at=START,
    )
    store.save_parent_portfolio_state(
        inactive, event_kind="initialized", actor="test"
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aigt", "portfolio", "soak", "evaluate",
            "--database", str(database),
            "--at", (START + timedelta(hours=73)).isoformat(),
        ],
    )
    main()
    evaluation = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aigt", "portfolio", "activate-paper",
            "--evaluation-id", evaluation["evaluation_id"],
            "--database", str(database),
        ],
    )
    main()
    activated = json.loads(capsys.readouterr().out)

    assert evaluation["status"] == "pass"
    assert activated["paper_active"] is True
    assert activated["entries_paused"] is False
    assert activated["soak_evaluation_id"] == evaluation["evaluation_id"]
