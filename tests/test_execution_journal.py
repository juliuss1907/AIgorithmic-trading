from datetime import datetime, timezone
from decimal import Decimal

import pytest

from intraday.execution.contracts import AccountRef, ExecutionUnavailable, OrderIntent, OrderUpdate
from intraday.execution.journal import ExecutionJournal, OrderCoordinator


NOW = datetime(2026, 9, 30, 3, tzinfo=timezone.utc)
ACCOUNT = AccountRef(venue="binance", environment="demo", account_id="test")


def intent(**changes):
    values = dict(intent_id="entry-1", account=ACCOUNT, symbol="BTCUSDT", market="perp",
                  side="BUY", quantity=Decimal("0.01"), created_at=NOW)
    values.update(changes)
    return OrderIntent(**values)


class Adapter:
    account_ref = ACCOUNT
    submits = 0
    result = None
    def submit(self, order):
        self.submits += 1
        raise ExecutionUnavailable("timeout")
    def query(self, order):
        return self.result


def test_unknown_submission_never_resends_even_after_restart(tmp_path):
    path = tmp_path / "execution.sqlite3"
    adapter = Adapter()
    coordinator = OrderCoordinator(ExecutionJournal(path), adapter)
    assert coordinator.submit(intent()).status == "UNKNOWN"
    assert adapter.submits == 1
    reopened = OrderCoordinator(ExecutionJournal(path), adapter)
    assert reopened.submit(intent()).status == "UNKNOWN"
    assert adapter.submits == 1
    adapter.result = OrderUpdate(intent_id="entry-1", status="FILLED", exchange_order_id="17",
                                 executed_quantity=Decimal("0.01"), average_price=Decimal("100000"), received_at=NOW)
    assert reopened.reconcile(intent()).status == "FILLED"
    assert adapter.submits == 1


def test_intent_must_be_immutable_and_saved_before_network(tmp_path):
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    class CheckingAdapter(Adapter):
        def submit(self, order):
            assert journal.order(order.account, order.intent_id)[0] == order
            return OrderUpdate(intent_id=order.intent_id, status="NEW", received_at=NOW)
    coordinator = OrderCoordinator(journal, CheckingAdapter())
    coordinator.submit(intent())
    with pytest.raises(ValueError, match="payload"):
        coordinator.submit(intent(quantity=Decimal("0.02")))


def test_cycle_lock_is_exclusive_and_released(tmp_path):
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    second = ExecutionJournal(journal.path)
    with journal.lock():
        with pytest.raises(ExecutionUnavailable, match="another"):
            with second.lock():
                pass
    with second.lock():
        pass


def test_journal_refuses_an_existing_source_database(tmp_path):
    import sqlite3
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE signals (id TEXT)")
    with pytest.raises(ValueError, match="execution"):
        ExecutionJournal(path)
