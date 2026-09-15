import pandas as pd
import pytest

from lab.contracts import SmaStrategySpec
from lab.paper import PaperTradingService


RULES = {"quantity_step": "0.00001000", "min_notional": "5.00000000"}


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


def test_buy_cycle_is_idempotent_and_reconciles(service):
    account = service.create_account(SmaStrategySpec(fast_window=1, slow_window=2))

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
    account = service.create_account(SmaStrategySpec(fast_window=1, slow_window=2))
    service.run_cycle(account["id"], bars([100, 120]), bid=100, ask=101)

    cycle = service.run_cycle(account["id"], bars([100, 120, 90]), bid=89, ask=90)
    state = service.get_account(account["id"])

    assert cycle["fill"]["side"] == "sell"
    assert state["btc_quantity"] == pytest.approx(0)
    assert service.reconcile(account["id"])["ok"] is True


def test_drawdown_halts_before_a_new_order(service):
    account = service.create_account(SmaStrategySpec(fast_window=1, slow_window=2))
    service.run_cycle(account["id"], bars([100, 120]), bid=100, ask=100)

    cycle = service.run_cycle(account["id"], bars([100, 120, 130]), bid=50, ask=51)
    state = service.get_account(account["id"])

    assert cycle["fill"] is None
    assert state["status"] == "halted"
    assert state["halt_reason"] == "drawdown_limit"
    assert state["drawdown"] <= -.20


def test_changed_exchange_rules_halt_the_account(service):
    account = service.create_account(SmaStrategySpec(fast_window=1, slow_window=2))
    changed = RULES | {"quantity_step": "0.00010000"}

    cycle = service.run_cycle(
        account["id"], bars([100, 120]), bid=100, ask=101,
        exchange_rules=changed,
    )

    assert cycle["fill"] is None
    assert service.get_account(account["id"])["halt_reason"] == "exchange_rules_changed"


def test_manual_kill_switch_is_persistent_and_audited(service):
    account = service.create_account(SmaStrategySpec(fast_window=1, slow_window=2))

    halted = service.halt_account(account["id"], reason="manual_kill_switch")

    assert halted["status"] == "halted"
    assert service.list_halts(account["id"])[0]["reason"] == "manual_kill_switch"
