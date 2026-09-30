from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from intraday.execution.contracts import AccountRef, OrderIntent, Quote
from intraday.execution.simulated import PaperExecutionAdapter


NOW = datetime(2026, 9, 30, 3, tzinfo=timezone.utc)
PAPER = AccountRef(venue="simulated", environment="paper", account_id="local")


def intent(**changes):
    payload = dict(
        intent_id="entry-1", account=PAPER, symbol="BTCUSDT", market="perp",
        side="BUY", quantity=Decimal("0.01"), created_at=NOW,
    )
    payload.update(changes)
    return OrderIntent(**payload)


def quote():
    return Quote(bid=Decimal("99990"), ask=Decimal("100010"), mark=Decimal("100000"), observed_at=NOW)


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN", "Infinity"])
def test_order_intent_rejects_invalid_quantity(quantity):
    with pytest.raises(ValidationError):
        intent(quantity=Decimal(quantity))


def test_protective_order_requires_reduction_and_trigger():
    with pytest.raises(ValidationError):
        intent(order_type="STOP_MARKET")
    assert intent(order_type="STOP_MARKET", reduce_only=True, stop_price=Decimal("98000"))


def test_paper_adapter_matches_existing_fee_and_slippage_assumptions():
    adapter = PaperExecutionAdapter(account=PAPER, quote=quote(), fee_bps=5, slippage_bps=5)
    update = adapter.submit(intent())
    assert update.status == "FILLED"
    assert update.executed_quantity == Decimal("0.01")
    assert float(update.fills[0].price) == pytest.approx(100010 * 1.0005)
    assert float(update.fills[0].commission) == pytest.approx(0.01 * 100010 * 1.0005 * 0.0005)
    assert adapter.query(intent()) == update


def test_paper_adapter_replays_same_intent_and_rejects_changed_payload():
    adapter = PaperExecutionAdapter(account=PAPER, quote=quote())
    first = adapter.submit(intent())
    assert adapter.submit(intent()) == first
    with pytest.raises(ValueError, match="payload"):
        adapter.submit(intent(quantity=Decimal("0.02")))


def test_adapter_rejects_another_account():
    adapter = PaperExecutionAdapter(account=PAPER, quote=quote())
    with pytest.raises(ValueError, match="account"):
        adapter.submit(intent(account=AccountRef(venue="binance", environment="demo", account_id="other")))
