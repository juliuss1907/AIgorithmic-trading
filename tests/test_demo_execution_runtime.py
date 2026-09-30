from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.contracts import DecisionScope, Direction, FeatureSnapshot, JevDecision, PerpRuleParameters, Regime, RiskLevel
from intraday.execution.contracts import AccountRef, AccountSnapshot, ExecutionFill, InstrumentRules, OrderUpdate, Position, Quote, OpenOrder
from intraday.execution.journal import ExecutionJournal
from intraday.execution.runtime import DemoRuntime


NOW = datetime(2026, 9, 30, 3, tzinfo=timezone.utc)
ACCOUNT = AccountRef(venue="binance", environment="demo", account_id="test")


class Rule:
    rule_id = "btc-perp-champion"
    parameters = PerpRuleParameters()


class Source:
    path = None
    def rule(self):
        return Rule()
    def evaluation(self, evaluation_id, *, now):
        if evaluation_id != "passed":
            raise ValueError("passing soak required")
    def latest_decision(self, *, now):
        snapshot = FeatureSnapshot.create(symbol="BTCUSDT", market="binance_usdm_perp", feature_schema_version="2",
            event_time=now, built_at=now, bid=99990, ask=100010, features={"mark_price": 100000}, freshness={"candles": True})
        return snapshot, JevDecision(decision_id="source-1", tick_id="tick-1", snapshot_id=snapshot.snapshot_id,
            direction=Direction.BUY, direction_confidence=.95, regime=Regime.TRENDING_UP, toxic_flow=.1,
            entry_quality=4, risk_level=RiskLevel.LOW, model_ref="model", created_at=now), self.rule().rule_id


class Venue:
    account_ref = ACCOUNT
    def __init__(self):
        self.quantity = Decimal(0)
        self.wallet = Decimal(10000)
        self.orders = {}
        self.stop_failure = False
        self.clock = NOW
    def check_clock(self, *, now):
        pass
    def quote(self, symbol, *, now):
        return Quote(bid=99990, ask=100010, mark=100000, observed_at=now)
    def instrument(self, symbol):
        return InstrumentRules(symbol=symbol, quantity_step=".001", min_quantity=".001", max_quantity="100", min_notional="100", price_tick=".1")
    def funding_since(self, since, *, now):
        return Decimal(0)
    def account_snapshot(self, *, now):
        positions = (Position(symbol="BTCUSDT", quantity=self.quantity, entry_price=100000, mark_price=100000),) if self.quantity else ()
        opens = tuple(OpenOrder(symbol="BTCUSDT", client_id=identity, order_type=order.order_type)
                      for identity, (order, update) in self.orders.items() if not update.terminal)
        return AccountSnapshot(account=ACCOUNT, wallet_balance=self.wallet, equity=self.wallet, available_balance=self.wallet,
            positions=positions, open_orders=opens, can_trade=True, one_way=True, single_asset=True,
            margin_mode="ISOLATED", leverage=3, observed_at=now)
    def submit(self, intent):
        if intent.order_type == "STOP_MARKET":
            if self.stop_failure:
                from intraday.execution.contracts import ExecutionUnavailable
                raise ExecutionUnavailable("stop rejected")
            update = OrderUpdate(intent_id=intent.intent_id, status="NEW", exchange_order_id=intent.intent_id, received_at=self.clock)
        else:
            self.quantity += intent.quantity * (1 if intent.side == "BUY" else -1)
            commission = intent.quantity * Decimal(100000) * Decimal(".0005")
            self.wallet -= commission
            fill = ExecutionFill(fill_id=intent.intent_id, quantity=intent.quantity, price=100000,
                                 commission=commission, commission_asset="USDT", filled_at=self.clock)
            update = OrderUpdate(intent_id=intent.intent_id, status="FILLED", exchange_order_id=intent.intent_id,
                                 executed_quantity=intent.quantity, average_price=100000, fills=(fill,), received_at=self.clock)
        self.orders[intent.intent_id] = (intent, update)
        return update
    def query(self, intent):
        saved = self.orders.get(intent.intent_id)
        return saved[1].model_copy(update={"received_at": self.clock}) if saved else None
    def cancel(self, intent):
        order, update = self.orders[intent.intent_id]
        update = update.model_copy(update={"status": "CANCELED", "received_at": self.clock})
        self.orders[intent.intent_id] = (order, update)
        return update


def runtime(tmp_path):
    venue = Venue()
    return DemoRuntime(ExecutionJournal(tmp_path / "demo.sqlite3"), venue, Source(), clock=lambda: venue.clock), venue


def test_disabled_until_explicit_activation_then_entry_and_protection(tmp_path):
    runner, venue = runtime(tmp_path)
    with pytest.raises(ValueError, match="activation"):
        runner.cycle(now=NOW)
    assert not venue.orders
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    assert not venue.orders
    result = runner.cycle(now=NOW)
    assert result["status"] == "protected"
    assert venue.quantity == Decimal(".002")
    assert runner.journal.control(ACCOUNT)["last_account"]["positions"][0]["quantity"] == "0.002"
    assert len(venue.orders) == 2
    assert runner.cycle(now=NOW)["status"] == "protected"
    assert len(venue.orders) == 2


def test_protection_failure_pauses_and_attempts_reduce_only_flatten(tmp_path):
    runner, venue = runtime(tmp_path)
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    venue.stop_failure = True
    result = runner.cycle(now=NOW)
    assert result["status"] == "paused"
    assert runner.journal.control(ACCOUNT)["paused"]
    assert venue.quantity == 0
    assert runner.journal.control(ACCOUNT)["last_account"]["positions"] == []
    reductions = [order for order, update in venue.orders.values() if order.order_type == "MARKET" and order.reduce_only]
    assert len(reductions) == 1


def test_missing_exchange_stop_triggers_flatten_and_blocks_reentry(tmp_path):
    runner, venue = runtime(tmp_path)
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    runner.cycle(now=NOW)
    stop = next(order for order, _ in venue.orders.values() if order.order_type == "STOP_MARKET")
    venue.cancel(stop)
    runner.cycle(now=NOW)
    assert venue.quantity == 0
    assert runner.journal.control(ACCOUNT)["paused"]


def test_manual_balance_reset_blocks_entries(tmp_path):
    runner, venue = runtime(tmp_path)
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    venue.wallet += 5000
    result = runner.cycle(now=NOW)
    assert result["status"] == "paused"
    assert result["reason"] == "account_cash_drift"
    assert not venue.orders


def test_activation_rejects_account_configuration_and_failed_evidence(tmp_path):
    runner, venue = runtime(tmp_path)
    with pytest.raises(ValueError):
        runner.activate(evaluation_id="failed", capital=Decimal(1000), now=NOW)
    original = venue.account_snapshot
    venue.account_snapshot = lambda **kwargs: original(**kwargs).model_copy(update={"leverage": 20})
    with pytest.raises(ValueError, match="isolated"):
        runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    assert runner.journal.control(ACCOUNT) is None


def test_protection_uses_current_clock_after_slow_entry_response(tmp_path):
    runner, venue = runtime(tmp_path)
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    original = venue.submit
    def delayed(intent):
        venue.clock += timedelta(seconds=3)
        return original(intent)
    venue.submit = delayed
    assert runner.cycle(now=NOW)["status"] == "protected"
    assert venue.quantity == Decimal(".002")
    assert not runner.journal.control(ACCOUNT)["paused"]


def test_partial_entry_is_cancelled_before_failed_stop_recovery(tmp_path):
    runner, venue = runtime(tmp_path)
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    original = venue.submit
    def partial(intent):
        if intent.order_type == "MARKET" and not intent.reduce_only:
            smaller = intent.model_copy(update={"quantity": intent.quantity / 2})
            update = original(smaller).model_copy(update={"status": "PARTIALLY_FILLED"})
            venue.orders[intent.intent_id] = (smaller, update)
            return update
        return original(intent)
    venue.submit = partial
    venue.stop_failure = True
    assert runner.cycle(now=NOW)["status"] == "paused"
    assert venue.quantity == 0
    entry = next(update for order, update in venue.orders.values() if order.order_type == "MARKET" and not order.reduce_only)
    assert entry.status == "CANCELED"
    assert entry.executed_quantity == Decimal(".001")


@pytest.mark.parametrize("first_close", ["REJECTED", "EXPIRED"])
def test_operator_flatten_recovers_after_terminal_unsuccessful_close(tmp_path, first_close):
    runner, venue = runtime(tmp_path)
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    runner.cycle(now=NOW)
    original = venue.submit
    failed = [False]
    def close_once(intent):
        if intent.order_type == "MARKET" and intent.reduce_only and not failed[0]:
            failed[0] = True
            if first_close == "EXPIRED":
                smaller = intent.model_copy(update={"quantity": intent.quantity / 2})
                update = original(smaller).model_copy(update={"status": "EXPIRED"})
            else:
                update = OrderUpdate(intent_id=intent.intent_id, status="REJECTED", received_at=venue.clock)
            venue.orders[intent.intent_id] = (intent, update)
            return update
        return original(intent)
    venue.submit = close_once
    assert runner.flatten(now=NOW)["status"] == "paused"
    assert venue.quantity != 0
    assert runner.flatten(now=NOW)["status"] == "flat"
    assert venue.quantity == 0
    assert runner.journal.control(ACCOUNT)["paused"]
    exits = [intent for intent, _ in venue.orders.values() if intent.order_type == "MARKET" and intent.reduce_only]
    assert len(exits) == 2 and exits[0].intent_id != exits[1].intent_id


def test_unknown_entry_protects_observed_position_without_inventing_fills_or_resubmitting(tmp_path):
    from intraday.execution.contracts import ExecutionUnavailable
    runner, venue = runtime(tmp_path)
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    original_submit, original_query = venue.submit, venue.query
    def lost_ack(intent):
        update = original_submit(intent)
        if intent.order_type == "MARKET":
            raise ExecutionUnavailable("timeout after exchange fill")
        return update
    def unavailable_lookup(intent):
        if intent.order_type == "MARKET":
            raise ExecutionUnavailable("temporarily unavailable")
        return original_query(intent)
    venue.submit, venue.query = lost_ack, unavailable_lookup
    result = runner.cycle(now=NOW)
    assert result["reason"] == "uncertain_entry_protected_reconcile_required"
    assert venue.quantity == Decimal(".002")
    assert len(venue.orders) == 2
    entry = next(update for order, update in runner.journal.orders(ACCOUNT) if order.order_type == "MARKET")
    assert entry.status == "UNKNOWN" and entry.executed_quantity == 0
    assert runner.journal.status()["fill_count"] == 0
    reopened = DemoRuntime(ExecutionJournal(runner.journal.path), venue, Source(), clock=lambda: venue.clock)
    assert reopened.cycle(now=NOW)["status"] == "paused"
    assert len(venue.orders) == 2


def test_exhausted_strategy_equity_keeps_reduce_only_exit_available(tmp_path):
    runner, venue = runtime(tmp_path)
    runner.activate(evaluation_id="passed", capital=Decimal(1000), now=NOW)
    runner.cycle(now=NOW)
    venue.wallet -= 2000
    assert runner.cycle(now=NOW)["status"] == "flat"
    assert venue.quantity == 0
    assert runner.journal.control(ACCOUNT)["paused"]
