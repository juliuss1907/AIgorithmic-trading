from datetime import datetime, timezone
from decimal import Decimal

import pytest

from intraday.execution.contracts import AccountRef, ExecutionFill, ExecutionUnavailable, OrderIntent, OrderUpdate
from intraday.execution.journal import ExecutionJournal


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def test_inventory_uses_only_confirmed_owned_fills_and_native_base_fees(tmp_path):
    journal = ExecutionJournal(tmp_path / "execution.sqlite")
    account = AccountRef(venue="binance", environment="demo", account_id="fake", market="spot")
    assert journal.spot_inventory(account, "DOGEUSDT")["quantity"] == 0
    def record(identity, side, qty, fee, asset):
        intent = OrderIntent(intent_id=identity, account=account, symbol="DOGEUSDT", market="spot", side=side,
                             quantity=qty, created_at=NOW)
        journal.prepare(intent)
        fill = ExecutionFill(fill_id=identity, quantity=qty, price=".2", commission=fee, commission_asset=asset, filled_at=NOW)
        journal.record(intent, OrderUpdate(intent_id=identity, status="FILLED", executed_quantity=qty, fills=(fill,), received_at=NOW))
    record("buy", "BUY", 100, ".1", "DOGE")
    inventory = journal.spot_inventory(account, "DOGEUSDT")
    assert inventory["quantity"] == Decimal("99.9") and inventory["cash_flow"] == -20
    record("sell", "SELL", "99.9", ".02", "USDT")
    assert journal.spot_inventory(account, "DOGEUSDT")["quantity"] == 0
    assert journal.spot_inventory(account, "ETHUSDT")["quantity"] == 0
    record("excess", "SELL", 1, 0, "USDT")
    with pytest.raises(ExecutionUnavailable, match="managed inventory"):
        journal.spot_inventory(account, "DOGEUSDT")


def test_account_namespaces_preserve_legacy_and_separate_markets():
    legacy = AccountRef(venue="binance", environment="demo", account_id="fake")
    assert legacy.key == "binance:demo:fake"
    for market in ("spot", "perp"):
        scoped = legacy.model_copy(update={"market": market})
        assert AccountRef.from_key(scoped.key) == scoped
        assert scoped != legacy
