import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from lab.contracts import EntryVolatilityPositionSizingSpec, SmaStrategySpec
from lab.paper import PaperTradingService


RULES = {"quantity_step": "0.00001000", "min_notional": "5.00000000"}
SNAPSHOT = "a" * 64


def bars(closes):
    index = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    close = pd.Series(closes, index=index, dtype=float)
    return pd.DataFrame({
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": 10,
    }, index=index)


@pytest.fixture
def service(tmp_path):
    return PaperTradingService(tmp_path / "paper.sqlite3", log_dir=tmp_path / "logs")


def create_account(service):
    return service.create_account(
        SmaStrategySpec(fast_window=1, slow_window=2), dataset_snapshot_id=SNAPSHOT
    )


def test_buy_cycle_is_idempotent_and_reconciles(service):
    account = create_account(service)

    first = service.run_cycle(account["id"], bars([100, 120]), bid=100, ask=101)
    repeated = service.run_cycle(account["id"], bars([100, 120]), bid=100, ask=101)
    state = service.get_account(account["id"])

    assert first["id"] == repeated["id"]
    assert first["signal"] == .5
    assert first["fill"]["side"] == "buy"
    assert first["fill"]["price"] == pytest.approx(101.0505)
    assert first["fill"]["fee"] > 0
    assert first["reconciliation_ok"] is True
    assert 0 < state["btc_quantity"]
    assert len(service.list_fills(account["id"])) == 1
    assert service.reconcile(account["id"])["ok"] is True


def test_exit_cycle_sells_the_entire_spot_position(service):
    account = create_account(service)
    service.run_cycle(account["id"], bars([100, 120]), bid=100, ask=101)

    cycle = service.run_cycle(account["id"], bars([100, 120, 90]), bid=89, ask=90)
    state = service.get_account(account["id"])

    assert cycle["fill"]["side"] == "sell"
    assert state["btc_quantity"] == pytest.approx(0)
    assert service.reconcile(account["id"])["ok"] is True


def test_drawdown_halts_before_a_new_order(service):
    account = create_account(service)
    service.run_cycle(account["id"], bars([100, 120]), bid=100, ask=100)

    cycle = service.run_cycle(account["id"], bars([100, 120, 130]), bid=50, ask=51)
    state = service.get_account(account["id"])

    assert cycle["fill"] is None
    assert state["status"] == "halted"
    assert state["halt_reason"] == "drawdown_limit"
    assert state["drawdown"] <= -.20


def test_changed_exchange_rules_halt_the_account(service):
    account = create_account(service)
    changed = RULES | {"quantity_step": "0.00010000"}

    cycle = service.run_cycle(
        account["id"], bars([100, 120]), bid=100, ask=101,
        exchange_rules=changed,
    )

    assert cycle["fill"] is None
    assert service.get_account(account["id"])["halt_reason"] == "exchange_rules_changed"


def test_manual_kill_switch_is_persistent_and_audited(service):
    account = create_account(service)

    halted = service.halt_account(account["id"], reason="manual_kill_switch")

    assert halted["status"] == "halted"
    assert service.list_halts(account["id"])[0]["reason"] == "manual_kill_switch"


def test_dashboard_collections_expose_the_audit_chain(service):
    account = create_account(service)
    service.run_cycle(account["id"], bars([100, 120]), bid=100, ask=101)

    assert service.list_accounts()[0]["dataset_snapshot_id"] == SNAPSHOT
    assert len(service.list_cycles(account["id"])) == 1
    assert len(service.list_signals(account["id"])) == 1
    assert len(service.list_intents(account["id"])) == 1
    assert len(service.list_fills(account["id"])) == 1
    assert len(service.list_ledger(account["id"])) == 2


def test_promoted_account_requires_selected_candidate_and_passing_holdout(service):
    class Gate:
        def __init__(self, state):
            self.state = state

        def get(self):
            return self.state

    with pytest.raises(ValueError, match="passing holdout"):
        service.create_promoted_account(
            Gate({"selected": "donchian_breakout", "candidate_lock": {}, "holdout": None}),
        )
    with pytest.raises(ValueError, match="candidate lock"):
        service.create_promoted_account(
            Gate({"selected": "donchian_breakout", "candidate_lock": None,
                  "holdout": {"passed": True, "dataset_id": SNAPSHOT}}),
        )

    lock = {
        "candidate": "donchian_breakout",
        "strategy": {"family": "donchian_breakout", "entry_window": 20,
                     "exit_window": 10, "atr_window": 14},
        "position_sizing": {"family": "entry_volatility", "lookback": 20,
                            "annual_target": .2, "annualization_days": 365},
    }
    created = service.create_promoted_account(Gate({
        "selected": "donchian_breakout", "candidate_lock": lock,
        "holdout": {"passed": True, "dataset_id": SNAPSHOT},
    }))
    assert created["status"] == "active"
    assert created["strategy"] == lock["strategy"]
    assert created["position_sizing"] == lock["position_sizing"]
    assert created["start_policy"] == "wait_for_new_entry"
    assert created["entry_armed"] is False


def promoted_gate():
    class Gate:
        def get(self):
            return {
                "status": "candidate_selected",
                "selected": "donchian_breakout",
                "candidate_lock": {
                    "candidate": "donchian_breakout", "run_id": "b" * 20,
                    "dataset_id": "c" * 64,
                    "strategy": {"family": "donchian_breakout", "entry_window": 20,
                                 "exit_window": 10, "atr_window": 14},
                    "position_sizing": {"family": "entry_volatility", "lookback": 20,
                                        "annual_target": .2, "annualization_days": 365},
                    "summary_sha256": "d" * 64, "provenance_sha256": "e" * 64,
                },
                "holdout": {
                    "candidate": "donchian_breakout", "passed": True,
                    "run_id": "f" * 20, "dataset_id": SNAPSHOT,
                    "summary_sha256": "1" * 64, "provenance_sha256": "2" * 64,
                },
            }

    return Gate()


def test_promoted_campaign_is_atomic_and_idempotent(service):
    first = service.create_promoted_account(promoted_gate(), exchange_rules=RULES)
    repeated = service.create_promoted_account(promoted_gate(), exchange_rules=RULES)

    campaign = service.get_campaign(first["id"])

    assert repeated["id"] == first["id"]
    assert campaign["account_id"] == first["id"]
    assert campaign["target_cycles"] == 56
    assert len(campaign["contract_sha256"]) == 64
    assert len(service.list_accounts()) == 1
    assert len(service.list_ledger(first["id"])) == 1


def test_campaign_progress_excludes_bootstrap_and_tracks_incidents(service):
    account = service.create_promoted_account(promoted_gate(), exchange_rules=RULES)
    frame = bars([100 + index for index in range(25)])
    service.run_cycle(account["id"], frame, bid=123, ask=124, entry_point="bootstrap")
    scheduler_frame = bars([100 + index for index in range(26)])
    service.run_cycle(
        account["id"], scheduler_frame, bid=124, ask=125, entry_point="scheduler"
    )
    incident = service.record_incident(
        account["id"], "2025-01-27", "data_fetch_failed", "temporary outage"
    )

    running = service.campaign_status(
        account["id"], now=datetime.now(timezone.utc) + timedelta(days=60)
    )
    service.acknowledge_incident(account["id"], incident["id"], "Network recovered")
    acknowledged = service.campaign_status(
        account["id"], now=datetime.now(timezone.utc) + timedelta(days=60)
    )

    assert running["successful_cycles"] == 1
    assert running["remaining_cycles"] == 55
    assert "unacknowledged_incidents" in running["blockers"]
    assert "unacknowledged_incidents" not in acknowledged["blockers"]
    assert service.list_incidents(account["id"])[0]["operator_note"] == "Network recovered"


def test_final_campaign_review_is_gated_and_immutable(service, monkeypatch):
    account = service.create_promoted_account(promoted_gate(), exchange_rules=RULES)

    with pytest.raises(ValueError, match="not eligible"):
        service.finalize_campaign(account["id"])

    eligible = service.campaign_status(account["id"]) | {
        "status": "eligible", "blockers": [], "successful_cycles": 56,
        "remaining_cycles": 0, "elapsed_days": 56, "round_trips": 1,
    }
    monkeypatch.setattr(service, "campaign_status", lambda *_args, **_kwargs: eligible)

    first = service.finalize_campaign(account["id"])
    repeated = service.finalize_campaign(account["id"])

    assert first == repeated
    assert first["status"] == "eligible"
    assert first["successful_cycles"] == 56
    assert len(first["review_sha256"]) == 64


def test_position_sizing_persists_across_restart_and_drives_cycle_target(tmp_path):
    database = tmp_path / "paper.sqlite3"
    service = PaperTradingService(database, log_dir=tmp_path / "logs")
    sizing = EntryVolatilityPositionSizingSpec()
    account = service.create_account(
        SmaStrategySpec(fast_window=1, slow_window=2), dataset_snapshot_id=SNAPSHOT,
        position_sizing=sizing,
    )
    volatile = bars([100 + (20 if index % 2 else 0) + index for index in range(24)])

    restarted = PaperTradingService(database, log_dir=tmp_path / "logs")
    cycle = restarted.run_cycle(account["id"], volatile, bid=120, ask=121)

    assert restarted.get_account(account["id"])["position_sizing"] == sizing.model_dump()
    assert 0 < cycle["signal"] < .5


def test_promoted_account_waits_for_flat_then_a_fresh_entry(service):
    class Gate:
        def get(self):
            return {
                "selected": "donchian_breakout",
                "candidate_lock": {
                    "candidate": "donchian_breakout",
                    "strategy": {"family": "donchian_breakout", "entry_window": 20,
                                 "exit_window": 10, "atr_window": 14},
                    "position_sizing": {"family": "entry_volatility", "lookback": 20,
                                        "annual_target": .2, "annualization_days": 365},
                },
                "holdout": {"passed": True, "dataset_id": SNAPSHOT},
            }

    account = service.create_promoted_account(Gate())
    rising = bars([100 + 3 * index for index in range(25)])
    existing_long = service.run_cycle(account["id"], rising, bid=171, ask=172)
    flat = service.run_cycle(
        account["id"], bars([*rising.close.tolist(), 50]), bid=49, ask=50,
    )
    fresh_entry = service.run_cycle(
        account["id"], bars([*rising.close.tolist(), 50, 200]), bid=199, ask=200,
    )

    assert existing_long["fill"] is None
    assert flat["fill"] is None
    assert fresh_entry["fill"]["side"] == "buy"
    assert service.get_account(account["id"])["entry_armed"] is True


def test_legacy_paper_account_migration_defaults_to_fixed_immediate(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    service = PaperTradingService(database, log_dir=tmp_path / "logs")
    account = create_account(service)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE paper_accounts RENAME TO paper_accounts_current")
        connection.execute(
            "CREATE TABLE paper_accounts AS SELECT id,strategy_json,dataset_snapshot_id,initial_cash,"
            "cash,btc_quantity,average_cost,equity,peak_equity,drawdown,max_target_weight,"
            "halt_drawdown,quantity_step,min_notional,fee_bps,slippage_bps,status,halt_reason,"
            "last_candle,created_at,updated_at FROM paper_accounts_current"
        )
        connection.execute("DROP TABLE paper_accounts_current")

    migrated = PaperTradingService(database, log_dir=tmp_path / "logs").get_account(account["id"])

    assert migrated["position_sizing"] == {"family": "fixed"}
    assert migrated["start_policy"] == "immediate"
    assert migrated["entry_armed"] is True
