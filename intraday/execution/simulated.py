"""Immediate local fills behind the same order interface as exchange adapters."""

from decimal import Decimal

from intraday.execution.contracts import AccountRef, ExecutionFill, OrderIntent, OrderUpdate, Quote


class PaperExecutionAdapter:
    def __init__(self, *, account: AccountRef, quote: Quote, fee_bps=5, slippage_bps=5):
        if account.environment != "paper" or fee_bps < 0 or slippage_bps < 0:
            raise ValueError("invalid paper execution configuration")
        self.account_ref = account
        self.execution_quote = quote
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self._orders: dict[str, tuple[OrderIntent, OrderUpdate]] = {}

    def _check(self, intent):
        if intent.account != self.account_ref:
            raise ValueError("execution account mismatch")
        previous = self._orders.get(intent.intent_id)
        if previous and previous[0] != intent:
            raise ValueError("intent id reused with a different payload")
        return previous

    def submit(self, intent: OrderIntent) -> OrderUpdate:
        previous = self._check(intent)
        if previous:
            return previous[1]
        if intent.order_type != "MARKET":
            raise ValueError("paper protective exits are evaluated by the deterministic runtime")
        reference = float(self.execution_quote.ask if intent.side == "BUY" else self.execution_quote.bid)
        # Preserve the historical simulator's float arithmetic and rounding behavior.
        price = reference * (1 + self.slippage_bps / 10_000 if intent.side == "BUY" else 1 - self.slippage_bps / 10_000)
        commission = float(intent.quantity) * price * (self.fee_bps / 10_000)
        fill = ExecutionFill(
            fill_id=intent.intent_id, quantity=intent.quantity, price=Decimal(str(price)),
            commission=Decimal(str(commission)), commission_asset="USDT", filled_at=intent.created_at,
        )
        update = OrderUpdate(
            intent_id=intent.intent_id, status="FILLED", exchange_order_id=intent.intent_id,
            executed_quantity=intent.quantity, average_price=fill.price, fills=(fill,),
            received_at=intent.created_at,
        )
        self._orders[intent.intent_id] = (intent, update)
        return update

    def query(self, intent: OrderIntent) -> OrderUpdate | None:
        previous = self._check(intent)
        return previous[1] if previous else None

    def cancel(self, intent: OrderIntent) -> OrderUpdate:
        previous = self._check(intent)
        if previous is None:
            raise ValueError("unknown paper order")
        return previous[1]
