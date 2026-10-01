"""Small BTC Perp runner: confirmed account risk, native protection, no paper fills."""

from __future__ import annotations

import hashlib
import uuid
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN

from intraday.contracts import DecisionScope
from intraday.execution.binance_demo import SYMBOL
from intraday.execution.contracts import ExecutionUnavailable, OrderIntent, TradingVenue
from intraday.execution.journal import ExecutionJournal, OrderCoordinator
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.scoped_gate import ScopedEntryGate


def _identity(*parts):
    return "ag" + hashlib.sha256(":".join(str(part) for part in parts).encode()).hexdigest()[:32]


class DemoRuntime:
    def __init__(self, journal: ExecutionJournal, venue: TradingVenue, source, *, clock=None):
        self.journal, self.venue, self.source = journal, venue, source
        self.account = venue.account_ref
        self.orders = OrderCoordinator(journal, venue)
        self.gate = ScopedEntryGate()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _compatible(snapshot):
        if (not snapshot.can_trade or not snapshot.one_way or not snapshot.single_asset
                or snapshot.margin_mode.upper() != "ISOLATED" or snapshot.leverage != 3):
            raise ValueError("Demo requires trading permission, One-way, Single-asset, isolated 3x; change settings manually")

    def preflight(self, *, now: datetime):
        self.venue.check_clock(now=now)
        snapshot = self.venue.account_snapshot(now=now)
        self._compatible(snapshot)
        quote = self.venue.quote(SYMBOL, now=now)
        instrument = self.venue.instrument(SYMBOL)
        rule = self.source.rule()
        return {"mode": "demo", "account": self.account.key, "account_snapshot": snapshot.model_dump(mode="json"),
                "quote": quote.model_dump(mode="json"), "instrument": instrument.model_dump(mode="json"),
                "rule_id": rule.rule_id, "orders_submitted": False,
                "clean_account": not snapshot.positions and not snapshot.open_orders}

    def activate(self, *, evaluation_id: str, capital: Decimal, now: datetime):
        if not capital.is_finite() or not 0 < capital <= 10000:
            raise ValueError("Demo strategy capital must be positive and at most 10000 USDT")
        with self.journal.lock():
            if self.journal.portfolio(self.account) is not None:
                raise ValueError("multi-route portfolio exists; use multi-route commands, not legacy BTC activation")
            self.source.evaluation(evaluation_id, now=now)
            report = self.preflight(now=now)
            if not report["clean_account"]:
                raise ValueError("activation requires a dedicated flat Demo account without open orders")
            if capital > Decimal(report["account_snapshot"]["available_balance"]):
                raise ValueError("insufficient Demo available balance for strategy capital")
            previous = self.journal.control(self.account)
            self._reconcile()
            if any(not update.terminal for _, update in self.journal.orders(self.account)):
                raise ValueError("unresolved execution intents block activation")
            if previous:
                if str(capital) != str(previous["capital"]) and capital != Decimal(previous["capital"]):
                    raise ValueError("reactivation cannot reset strategy capital or risk history")
                if previous["source_database"] != str(self.source.path):
                    raise ValueError("reactivation must use the original source database")
                control = dict(previous)
                # Retain capital, campaign, cash baseline and risk watermarks across pauses.
                snapshot = self.venue.account_snapshot(now=now)
                self._cash_matches(control, snapshot, now=now)
            else:
                control = {
                    "campaign_id": uuid.uuid4().hex, "capital": str(capital),
                    "initial_wallet": report["account_snapshot"]["wallet_balance"],
                    "started_at": now.isoformat(), "day": now.date().isoformat(),
                    "day_start_equity": float(capital), "high_water_mark": float(capital),
                    "source_database": str(self.source.path), "entry_id": None,
                }
            control.update(enabled=True, paused=False, reason=None, evaluation_id=evaluation_id,
                           rule_id=report["rule_id"], stop_distance=self.source.rule().parameters.stop_distance_pct)
            self.journal.save_control(self.account, control, kind="operator_activate", now=now)
            return {"status": "activated", "account": self.account.key, "capital": str(capital), "orders_submitted": False}

    def _control(self):
        if self.journal.portfolio(self.account) is not None:
            raise ValueError("legacy BTC runtime disabled after multi-route configuration")
        control = self.journal.control(self.account)
        if control is None or not control["enabled"]:
            raise ValueError("Demo requires explicit operator activation")
        if self.source is not None and control["source_database"] != str(self.source.path):
            raise ValueError("Demo source database differs from activated campaign")
        return control

    def _pause(self, control, reason, *, now):
        control.update(paused=True, reason=reason)
        self.journal.save_control(self.account, control, kind="pause", now=now)
        return {"status": "paused", "reason": reason, "account": self.account.key}

    def _observe(self, control, snapshot, *, now):
        control["last_account"] = snapshot.model_dump(mode="json")
        control["last_sync_at"] = self.clock().isoformat()
        self.journal.save_control(self.account, control, kind="account_observed", now=now)

    def pause(self, *, now):
        with self.journal.lock():
            return self._pause(self._control(), "operator_pause", now=now)

    def _reconcile(self):
        for intent, update in self.journal.orders(self.account):
            filled = sum((fill.quantity for fill in update.fills), Decimal(0))
            if not update.terminal or filled != update.executed_quantity:
                self.orders.reconcile(intent)

    def _cancel_live_market(self):
        for intent, update in self.journal.orders(self.account):
            if intent.order_type == "MARKET" and update.status in {"NEW", "PARTIALLY_FILLED"}:
                self.orders.cancel(intent)

    def _expected_quantity(self):
        return sum((update.executed_quantity * (1 if intent.side == "BUY" else -1)
                    for intent, update in self.journal.orders(self.account)), Decimal(0))

    def _cash_matches(self, control, snapshot, *, now):
        funding = self.venue.funding_since(datetime.fromisoformat(control["started_at"]), now=now)
        expected = Decimal(control["initial_wallet"]) + self.journal.cash_pnl(self.account) + funding
        if abs(snapshot.wallet_balance - expected) > Decimal("0.02"):
            raise ExecutionUnavailable("account_cash_drift")

    def _pending_market(self):
        return any(intent.order_type == "MARKET" and (
            not update.terminal or sum((fill.quantity for fill in update.fills), Decimal(0)) != update.executed_quantity
        ) for intent, update in self.journal.orders(self.account))

    def _state(self, control, snapshot, quote, *, now):
        capital = float(control["capital"])
        equity = capital + float(snapshot.equity - Decimal(control["initial_wallet"]))
        if equity <= 0:
            raise ExecutionUnavailable("allocated_equity_exhausted")
        position = snapshot.position(SYMBOL)
        quantity = float(position.quantity) if position else 0
        price = float(quote.mark)
        unrealized = quantity * (price - float(position.entry_price)) if position else 0
        if control["day"] != now.date().isoformat():
            control.update(day=now.date().isoformat(), day_start_equity=equity)
        control["high_water_mark"] = max(control["high_water_mark"], equity)
        return ParentPortfolioState(
            initial_equity=capital, realized_pnl=equity-capital-unrealized,
            perp_quantity=quantity, perp_entry_price=float(position.entry_price) if position else None,
            mark_price=price, spot_price=price, perp_mark_price=price,
            day_start_equity=control["day_start_equity"], high_water_mark=control["high_water_mark"],
            # Compatibility projection only; no existing simulated-paper flag is changed.
            paper_active=True, entries_paused=control["paused"], halt_reason=control["reason"], updated_at=now,
        )

    def _stop(self, control, position, *, now):
        rules = self.venue.instrument(SYMBOL)
        trigger = position.entry_price * (1 + Decimal(str(control["stop_distance"])) * (-1 if position.quantity > 0 else 1))
        rounding = ROUND_CEILING if position.quantity > 0 else ROUND_DOWN
        trigger = (trigger / rules.price_tick).to_integral_value(rounding=rounding) * rules.price_tick
        return OrderIntent(
            intent_id=_identity(control["campaign_id"], control["entry_id"], "stop"), account=self.account,
            symbol=SYMBOL, market="perp", side="SELL" if position.quantity > 0 else "BUY",
            quantity=abs(position.quantity), reduce_only=True, order_type="STOP_MARKET", stop_price=trigger, created_at=now,
        )

    def _protect(self, control, snapshot, *, now):
        position = snapshot.position(SYMBOL)
        desired = self._stop(control, position, now=now)
        saved = self.journal.order(self.account, desired.intent_id)
        if saved is None:
            update = self.orders.submit(desired)
        else:
            intent, update = saved
            if (intent.quantity != desired.quantity or intent.side != desired.side or intent.stop_price != desired.stop_price):
                return False
            update = self.orders.reconcile(intent)
        confirmed = self.venue.account_snapshot(now=now)
        self._observe(control, confirmed, now=now)
        checked_at = self.clock()
        return (update.status == "NEW" and -2 <= (checked_at - update.received_at).total_seconds() <= 20
                and any(order.client_id == desired.intent_id and order.symbol == SYMBOL
                        and order.order_type == "STOP_MARKET" for order in confirmed.open_orders))

    def _protect_uncertain_entry(self, control, snapshot, *, now):
        """Protect observed exposure without treating a timeout/position as a confirmed fill."""
        position = snapshot.position(SYMBOL)
        if not position or not control["entry_id"]:
            return False
        saved = self.journal.order(self.account, control["entry_id"])
        if saved is None:
            return False
        intent, update = saved
        if (update.status != "UNKNOWN" or intent.reduce_only or intent.order_type != "MARKET"
                or (intent.side == "BUY") != (position.quantity > 0) or abs(position.quantity) > intent.quantity):
            return False
        return self._protect(control, snapshot, now=now)

    def _clean_stops(self):
        for intent, update in self.journal.orders(self.account):
            if intent.order_type == "STOP_MARKET" and not update.terminal:
                if not self.orders.cancel(intent).terminal:
                    return False
        return True

    def _flatten(self, control, snapshot, *, now, reason):
        self._cancel_live_market()
        snapshot = self.venue.account_snapshot(now=now)
        position = snapshot.position(SYMBOL)
        if any(intent.order_type == "MARKET" and not update.terminal
               for intent, update in self.journal.orders(self.account)):
            return self._pause(control, "unresolved_market_order", now=now)
        if position:
            rules = self.venue.instrument(SYMBOL)
            rules.validate_quantity(abs(position.quantity), position.mark_price, reducing=True)
            exits = [intent for intent, update in self.journal.orders(self.account)
                     if intent.order_type == "MARKET" and intent.reduce_only]
            desired = OrderIntent(
                intent_id=_identity(control["campaign_id"], control["entry_id"], "close", str(position.quantity),
                                    exits[-1].intent_id if exits else "first"), account=self.account,
                symbol=SYMBOL, market="perp", side="SELL" if position.quantity > 0 else "BUY",
                quantity=abs(position.quantity), reduce_only=True, source_id=reason, created_at=now,
            )
            saved = self.journal.order(self.account, desired.intent_id)
            update = self.orders.reconcile(saved[0]) if saved else self.orders.submit(desired)
            confirmed = self.venue.account_snapshot(now=now)
            self._observe(control, confirmed, now=now)
            if update.status != "FILLED" or confirmed.position(SYMBOL):
                return self._pause(control, "flatten_unconfirmed", now=now)
        if not self._clean_stops():
            return self._pause(control, "protective_cancel_unconfirmed", now=now)
        self._observe(control, self.venue.account_snapshot(now=now), now=now)
        self.journal.save_control(self.account, control, kind="flatten_" + reason, now=now)
        return {"status": "flat", "reason": reason, "account": self.account.key}

    def flatten(self, *, now):
        with self.journal.lock():
            control = self._control()
            self._pause(control, "operator_flatten", now=now)
            self._reconcile()
            return self._flatten(control, self.venue.account_snapshot(now=now), now=now, reason="operator")

    def cycle(self, *, now):
        with self.journal.lock():
            control = self._control()
            try:
                return self._cycle(control, now=now)
            except Exception:
                # Backend/protocol surprises must halt entries, never leave the
                # campaign enabled for a blind next-step retry. Preserve intents.
                return self._pause(control, "venue_or_state_unavailable", now=now)

    def _cycle(self, control, *, now):
        started = time.monotonic()
        self._reconcile()
        self._cancel_live_market()
        snapshot = self.venue.account_snapshot(now=now)
        self._observe(control, snapshot, now=now)
        self._compatible(snapshot)
        known = {intent.intent_id for intent, _ in self.journal.orders(self.account)}
        if any(order.symbol != SYMBOL or order.client_id not in known for order in snapshot.open_orders):
            return self._pause(control, "unmanaged_open_order", now=now)
        if any(position.symbol != SYMBOL for position in snapshot.positions):
            return self._pause(control, "unmanaged_position", now=now)
        position = snapshot.position(SYMBOL)
        quantity = position.quantity if position else Decimal(0)
        if quantity != self._expected_quantity():
            if self._protect_uncertain_entry(control, snapshot, now=now):
                return self._pause(control, "uncertain_entry_protected_reconcile_required", now=now)
            return self._pause(control, "account_position_drift", now=now)
        if Decimal(control["capital"]) + snapshot.equity - Decimal(control["initial_wallet"]) <= 0:
            self._pause(control, "allocated_equity_exhausted", now=now)
            return self._flatten(control, snapshot, now=now, reason="loss_limit")
        quote = self.venue.quote(SYMBOL, now=now)
        state = self._state(control, snapshot, quote, now=now)
        control["last_account"] = snapshot.model_dump(mode="json")
        control["last_sync_at"] = now.isoformat()
        self.journal.save_control(self.account, control, kind="account_sync", now=now)
        if position:
            signed_return = (quote.mark / position.entry_price - 1) * (1 if quantity > 0 else -1)
            policy = self.gate.coordinator.policy
            if state.drawdown <= -policy.max_drawdown_pct or state.daily_return <= -policy.daily_loss_limit_pct:
                self._pause(control, "portfolio_loss_limit", now=now)
                return self._flatten(control, snapshot, now=now, reason="loss_limit")
            if signed_return <= -Decimal(str(control["stop_distance"])):
                return self._flatten(control, snapshot, now=now, reason="stop_loss")
            if not self._protect(control, snapshot, now=now):
                self._pause(control, "protective_stop_unconfirmed", now=now)
                self._flatten(control, snapshot, now=now, reason="protection_failure")
                return {"status": "paused", "reason": control["reason"], "account": self.account.key}
        elif not self._clean_stops():
            return self._pause(control, "protective_cancel_unconfirmed", now=now)
        if self._pending_market():
            return self._pause(control, "unresolved_market_order", now=now)
        try:
            self._cash_matches(control, snapshot, now=now)
        except ExecutionUnavailable:
            return self._pause(control, "account_cash_drift", now=now)
        if control["paused"]:
            return {"status": "paused", "reason": control["reason"], "account": self.account.key}
        try:
            rule = self.source.rule()
            self.source.evaluation(control["evaluation_id"], now=now)
            if rule.rule_id != control["rule_id"]:
                return self._pause(control, "champion_changed_requires_reactivation", now=now)
            research, decision, rule_id = self.source.latest_decision(now=self.clock())
        except (ValueError, KeyError, TypeError):
            return {"status": "protected" if position else "waiting", "reason": "no_eligible_source_signal"}
        if rule_id != rule.rule_id:
            return {"status": "protected" if position else "waiting", "reason": "signal_rule_mismatch"}
        research_price = Decimal(str(research.features["mark_price"]))
        if abs(quote.mark / research_price - 1) > Decimal(".01") or (quote.ask / quote.bid - 1) > Decimal(".001"):
            return {"status": "protected" if position else "waiting", "reason": "demo_price_dislocation"}
        authorization = self.gate.perp_entry(state, decision, rule.parameters)
        if not authorization.allowed:
            return {"status": "protected" if position else "waiting", "reason": list(authorization.reason_codes)}
        if position:
            if authorization.target_notional == 0:
                return self._flatten(control, snapshot, now=now, reason="signal_exit")
            return {"status": "protected", "account": self.account.key}  # No pyramiding in first rollout.
        if authorization.target_notional == 0:
            return {"status": "flat", "account": self.account.key}
        if time.monotonic() - started > 20:
            return {"status": "waiting", "reason": "execution_preflight_too_slow"}
        rules = self.venue.instrument(SYMBOL)
        # Cap growth to the operator's allocation; profits don't enlarge capital automatically.
        target = min(abs(Decimal(str(authorization.target_notional))), Decimal(control["capital"]) * Decimal(".2"))
        size = rules.round_quantity(target / quote.mark)
        try:
            rules.validate_quantity(size, quote.mark)
        except ValueError:
            return {"status": "waiting", "reason": "quantity_below_exchange_filters"}
        if size * quote.ask * Decimal(".335") > snapshot.available_balance:
            return {"status": "waiting", "reason": "insufficient_available_margin"}
        identity = _identity(control["campaign_id"], decision.decision_id, "entry")
        if self.journal.order(self.account, identity):
            return {"status": "waiting", "reason": "source_signal_already_consumed"}
        control["entry_id"] = identity
        self.journal.save_control(self.account, control, kind="entry_intent", now=now)
        order = OrderIntent(intent_id=identity, account=self.account, symbol=SYMBOL, market="perp",
                            side="BUY" if authorization.target_notional > 0 else "SELL", quantity=size,
                            source_id=decision.decision_id, created_at=now)
        update = self.orders.submit(order)
        if update.status in {"NEW", "PARTIALLY_FILLED"}:
            update = self.orders.cancel(order)
        confirmed = self.venue.account_snapshot(now=now)
        self._observe(control, confirmed, now=now)
        if confirmed.position(SYMBOL) and self._expected_quantity() == confirmed.position(SYMBOL).quantity:
            if not self._protect(control, confirmed, now=now):
                self._pause(control, "protective_stop_unconfirmed", now=now)
                self._flatten(control, confirmed, now=now, reason="protection_failure")
                return {"status": "paused", "reason": control["reason"], "account": self.account.key}
        else:
            if self._protect_uncertain_entry(control, confirmed, now=now):
                return self._pause(control, "uncertain_entry_protected_reconcile_required", now=now)
            return self._pause(control, "entry_position_unconfirmed", now=now)
        if update.status != "FILLED" or self._pending_market():
            return self._pause(control, "entry_fill_unconfirmed", now=now)
        return {"status": "protected", "account": self.account.key, "entry_id": identity}
