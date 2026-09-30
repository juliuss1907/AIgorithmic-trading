"""Serialized multi-route Demo execution with managed-only Spot ownership.

The source database is read-only. Portfolio activation and allocation are explicit
operator actions, never consequences of connecting credentials or adding a coin.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import hashlib
import uuid

from intraday.contracts import DecisionScope
from intraday.execution.allocation import DemoAllocation
from intraday.execution.contracts import ExecutionUnavailable, OrderIntent
from intraday.execution.journal import OrderCoordinator
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.scoped_gate import ScopedEntryGate


def identity(*parts):
    return "ag"+hashlib.sha256(":".join(map(str,parts)).encode()).hexdigest()[:32]


class MultiDemoRuntime:
    def __init__(self,journal,account,venue_factory,source_factory,*,clock=None):
        if account.market is not None:
            raise ValueError("portfolio identity must be the base account fingerprint")
        self.journal,self.account = journal,account
        self.venue_factory,self.source_factory = venue_factory,source_factory
        self.clock = clock or (lambda:datetime.now(timezone.utc))
        self.gate = ScopedEntryGate()

    def _save(self,p,kind):
        self.journal.save_portfolio(self.account,p,now=self.clock(),kind=kind)

    def _portfolio(self):
        p = self.journal.portfolio(self.account)
        if not p:
            raise ValueError("configure explicit coin weights before Demo execution")
        return p

    def _orders(self,venue):
        return [(i,u) for i,u in self.journal.orders(venue.account_ref) if i.symbol == venue.symbol]

    def _reconcile(self,venue):
        orders = OrderCoordinator(self.journal,venue)
        for intent,update in self._orders(venue):
            if not update.terminal or sum((f.quantity for f in update.fills),Decimal(0)) != update.executed_quantity:
                orders.reconcile(intent)
        for intent,update in self._orders(venue):
            if intent.order_type == "MARKET" and update.status in {"NEW","PARTIALLY_FILLED"}:
                orders.cancel(intent)

    def _pending(self,venue):
        return any(not u.terminal or sum((f.quantity for f in u.fills),Decimal(0)) != u.executed_quantity
                   for i,u in self._orders(venue) if i.order_type == "MARKET")

    def _quantity(self,venue,market):
        if market == "spot":
            return self.journal.spot_inventory(venue.account_ref,venue.symbol,require_valued_fees=False)["quantity"]
        return sum((u.executed_quantity*(1 if i.side == "BUY" else -1) for i,u in self._orders(venue)),Decimal(0))

    def _account_check(self,venue,market,snapshot):
        if snapshot.account != venue.account_ref or snapshot.can_trade is not True:
            raise ExecutionUnavailable("Demo account or trading permission mismatch")
        if market == "perp" and (not snapshot.one_way or not snapshot.single_asset
                                  or snapshot.margin_mode.upper() != "ISOLATED" or snapshot.leverage != 3):
            raise ValueError("Perp requires One-way, Single-asset, isolated 3x for this coin; change manually")
        known = {i.intent_id:i for i,u in self.journal.orders(venue.account_ref)}
        if any(o.client_id not in known or known[o.client_id].symbol != o.symbol for o in snapshot.open_orders):
            raise ExecutionUnavailable("unmanaged Demo open order")
        if market == "perp":
            for position in snapshot.positions:
                expected = sum((u.executed_quantity*(1 if i.side=="BUY" else -1)
                                for i,u in self.journal.orders(venue.account_ref) if i.symbol==position.symbol),Decimal(0))
                if position.quantity != expected:
                    raise ExecutionUnavailable("unmanaged Perp position or execution drift")
            expected = self._quantity(venue,market)
            actual = snapshot.position(venue.symbol)
            if expected != (actual.quantity if actual else Decimal(0)):
                raise ExecutionUnavailable("Perp quantity differs from journal")
        elif snapshot.balance(venue.symbol[:-4]).free+snapshot.balance(venue.symbol[:-4]).locked < self._quantity(venue,market):
            raise ExecutionUnavailable("managed Spot balance missing")

    def configure(self,plan:DemoAllocation,*,now):
        with self.journal.lock():
            legacy = self.journal.control(self.account)
            if legacy and legacy.get("paused") is not True:
                raise ValueError("pause and flatten legacy BTC execution before multi-route configuration")
            for ref in (self.account,self.account.model_copy(update={"market":"spot"}),self.account.model_copy(update={"market":"perp"})):
                recorded = self.journal.orders(ref)
                if any(not u.terminal or sum((f.quantity for f in u.fills),Decimal(0)) != u.executed_quantity for i,u in recorded):
                    raise ValueError("pause, flatten and reconcile terminal intents before configuration")
                for symbol in {i.symbol for i,u in recorded}:
                    qty = (self.journal.spot_inventory(ref,symbol)["quantity"] if ref.market=="spot" else
                           sum((u.executed_quantity*(1 if i.side=="BUY" else -1) for i,u in recorded if i.symbol==symbol),Decimal(0)))
                    if qty:
                        raise ValueError("pause and flatten before changing allocation")
            previous = self.journal.portfolio(self.account)
            if previous and (previous.get("paused") is not True or Decimal(previous["allocation"]["capital"]) != plan.capital):
                raise ValueError("allocation changes require a paused flat portfolio; capital/risk history cannot reset")
            baseline = {}
            source_path = None
            for symbol,market in plan.routes():
                source = self.source_factory(symbol,market)
                source.route()
                if source_path is not None and str(source.path) != source_path:
                    raise ValueError("all routes must use one read-only source database")
                source_path = str(source.path)
                venue = self.venue_factory(symbol,market)
                venue.check_clock(now=now)
                snapshot = venue.account_snapshot(now=now)
                self._account_check(venue,market,snapshot)
                if snapshot.open_orders or market=="perp" and snapshot.positions:
                    raise ValueError("configuration requires flat Perp and no exchange open orders")
                available = snapshot.balance("USDT").free if market=="spot" else snapshot.available_balance
                if available < plan.capital*(Decimal(".60") if market=="spot" else Decimal(".40")):
                    raise ValueError("insufficient available USDT for the requested sleeve budget")
                baseline[market] = str(snapshot.balance("USDT").free if market=="spot" else snapshot.wallet_balance)
            p = previous or {"campaign_id":uuid.uuid4().hex,"started_at":now.isoformat(),"routes":{},
                             "day":now.date().isoformat(),"day_start_equity":str(plan.capital),"high_water_mark":str(plan.capital),
                             "baseline":baseline,"baseline_started":{m:now.isoformat() for m in baseline}}
            if previous and previous["source_database"] != source_path:
                raise ValueError("allocation cannot change the campaign source database")
            for market,value in baseline.items():
                if market not in p["baseline"]:
                    p["baseline"][market] = value
                    p["baseline_started"][market] = now.isoformat()
            p.update(allocation=plan.model_dump(mode="json"),paused=True,enabled=False,reason="operator_configuration",source_database=source_path)
            for route in p["routes"].values():
                route["active"] = False
            self._save(p,"operator_allocation")
            return {"status":"configured","allocation":p["allocation"],"orders_submitted":False,"paused":True}

    def preflight(self,symbol,market,*,now):
        p = self._portfolio();plan = DemoAllocation.model_validate(p["allocation"])
        cap = plan.target_cap(symbol,market)
        source = self.source_factory(symbol,market)
        if str(source.path) != p["source_database"]:
            raise ValueError("source differs from configured campaign")
        rule = source.rule()
        venue = self.venue_factory(symbol,market)
        venue.check_clock(now=now)
        snapshot = venue.account_snapshot(now=now)
        self._account_check(venue,market,snapshot)
        instrument,quote = venue.instrument(symbol),venue.quote(symbol,now=now)
        return {"symbol":symbol,"market":market,"target_notional_cap":str(cap),"rule_id":rule.rule_id,
                "account":snapshot.model_dump(mode="json"),"instrument":instrument.model_dump(mode="json"),
                "quote":quote.model_dump(mode="json"),"orders_submitted":False}

    def activate(self,symbol,market,*,evaluation_id,now):
        with self.journal.lock():
            p = self._portfolio()
            report = self.preflight(symbol,market,now=now)
            source = self.source_factory(symbol,market)
            source.evaluation(evaluation_id,now=now)
            self._risk(p,now=now)
            venue = self.venue_factory(symbol,market)
            self._reconcile(venue)
            if self._pending(venue):
                raise ValueError("reconcile uncertain orders before activation")
            if self._quantity(venue,market):
                raise ValueError("route must be flat before explicit activation")
            # Allocated risk history is never reset by reactivation.
            route = {"symbol":symbol,"market":market,"active":True,"evaluation_id":evaluation_id,
                     "rule_id":report["rule_id"],"stop_distance":float(DemoAllocation.model_validate(p["allocation"]).spot_emergency_stop_pct)
                     if market=="spot" else source.rule().parameters.stop_distance_pct,
                     "entry_id":None,"consumed_bar":None}
            previous = p["routes"].get(market+":"+symbol)
            if previous:
                route["consumed_bar"] = previous.get("consumed_bar")
            p["routes"][market+":"+symbol] = route
            p.update(enabled=True,paused=False,reason=None)
            self._save(p,"operator_route_activate")
            return {"status":"activated","symbol":symbol,"market":market,"orders_submitted":False}

    def pause(self,*,now,reason="operator_pause"):
        with self.journal.lock():
            p = self._portfolio();p.update(paused=True,reason=reason)
            self._save(p,"operator_portfolio_pause")
            return {"status":"paused","native_stops_retained":True}

    def _risk(self,p,*,now):
        plan = DemoAllocation.model_validate(p["allocation"])
        total,spot_gross,perp_gross = plan.capital,Decimal(0),Decimal(0)
        markets = {}
        symbols = {"spot":set(),"perp":set()}
        for market in symbols:
            ref = self.account.model_copy(update={"market":market})
            symbols[market] = {i.symbol for i,u in self.journal.orders(ref)}
            symbols[market].update(s for s,m in plan.routes() if m==market)
            for symbol in symbols[market]:
                venue = self.venue_factory(symbol,market)
                self._reconcile(venue)
                snapshot = venue.account_snapshot(now=now)
                self._account_check(venue,market,snapshot)
                if self._pending(venue):
                    raise ExecutionUnavailable("unresolved market order requires reconciliation")
                quote = venue.quote(symbol,now=now)
                markets[(symbol,market)] = (venue,snapshot,quote)
                if market=="spot":
                    owned = self.journal.spot_inventory(venue.account_ref,symbol)
                    total += owned["cash_flow"]+owned["quantity"]*quote.mark
                    spot_gross += owned["quantity"]*quote.mark
                else:
                    position = snapshot.position(symbol)
                    if position:
                        total += position.quantity*(quote.mark-position.entry_price)
                        perp_gross += abs(position.quantity)*quote.mark
        if symbols["spot"]:
            venue,snapshot,_ = markets[(next(iter(symbols["spot"])),"spot")]
            cash = sum((self.journal.spot_inventory(venue.account_ref,s)["cash_flow"] for s in symbols["spot"]),Decimal(0))
            if abs(snapshot.balance("USDT").free+snapshot.balance("USDT").locked-Decimal(p["baseline"]["spot"])-cash) > Decimal(".02"):
                raise ExecutionUnavailable("Spot USDT cash drift; pre-existing holdings are not adopted")
        if symbols["perp"]:
            venue,snapshot,_ = markets[(next(iter(symbols["perp"])),"perp")]
            funding = sum((markets[(s,"perp")][0].funding_since(datetime.fromisoformat(p["baseline_started"]["perp"]),now=now)
                           for s in symbols["perp"]),Decimal(0))
            cash = self.journal.cash_pnl(venue.account_ref)+funding
            if abs(snapshot.wallet_balance-Decimal(p["baseline"]["perp"])-cash) > Decimal(".02"):
                raise ExecutionUnavailable("Perp account cash drift")
            total += cash
        if p["day"] != now.date().isoformat():
            p.update(day=now.date().isoformat(),day_start_equity=str(total))
        p["high_water_mark"] = str(max(Decimal(p["high_water_mark"]),total))
        if total <= 0 or total <= Decimal(p["day_start_equity"])*Decimal(".985") or total <= Decimal(p["high_water_mark"])*Decimal(".92"):
            raise ExecutionUnavailable("portfolio_loss_limit")
        return total,spot_gross,perp_gross,markets

    def _clean_stops(self,venue):
        coordinator = OrderCoordinator(self.journal,venue)
        for intent,update in self._orders(venue):
            if intent.order_type != "MARKET" and not update.terminal:
                if not coordinator.cancel(intent).terminal:
                    raise ExecutionUnavailable("protective cancellation not confirmed")

    def _protect(self,p,route,venue,snapshot,quote,*,now,observed_perp_quantity=None):
        market,symbol = route["market"],route["symbol"]
        qty = self._quantity(venue,market) if observed_perp_quantity is None else observed_perp_quantity
        if observed_perp_quantity is not None and market != "perp":
            raise ValueError("uncertain observed protection is Perp reduce-only, never Spot inventory adoption")
        if not qty:
            self._clean_stops(venue);return
        if market == "perp":
            entry = snapshot.position(symbol).entry_price
        else:
            owned = self.journal.spot_inventory(venue.account_ref,symbol,require_valued_fees=False)
            entry = owned["entry_price"]
        rules = venue.instrument(symbol,order_type="STOP_LOSS") if market=="spot" else venue.instrument(symbol)
        amount = rules.round_quantity(abs(qty))
        if market=="perp" and amount != abs(qty) or amount<=0:
            raise ExecutionUnavailable("protective quantity cannot match managed position")
        trigger = entry*(1-Decimal(str(route["stop_distance"]))*(1 if qty>0 else -1))
        trigger = (trigger/rules.price_tick).to_integral_value(rounding=ROUND_CEILING if qty>0 else ROUND_DOWN)*rules.price_tick
        rules.validate_price(trigger)
        rules.validate_quantity(amount,trigger,reducing=market=="perp")
        if (qty>0 and quote.bid<=trigger) or (qty<0 and quote.ask>=trigger):
            self._close(p,route,venue,now=now,reason="stop_loss");return
        stop_id = identity(p["campaign_id"],route["entry_id"],market,symbol,"stop",str(amount),str(trigger))
        order = self.journal.order(venue.account_ref,stop_id)
        coordinator = OrderCoordinator(self.journal,venue)
        if order:
            update = coordinator.reconcile(order[0])
        else:
            self._clean_stops(venue)
            # Cancelling an old stop can race a fill; recompute before replacing.
            if observed_perp_quantity is None and self._quantity(venue,market) != qty:
                raise ExecutionUnavailable("inventory changed while replacing protection")
            intent = OrderIntent(intent_id=stop_id,account=venue.account_ref,symbol=symbol,market=market,
                                 side="SELL" if qty>0 else "BUY",quantity=amount,order_type="STOP_LOSS" if market=="spot" else "STOP_MARKET",
                                 reduce_only=market=="perp",stop_price=trigger,created_at=now)
            update = coordinator.submit(intent)
        confirmed = venue.account_snapshot(now=now)
        if update.status != "NEW" or not any(o.client_id==stop_id and o.symbol==symbol for o in confirmed.open_orders):
            raise ExecutionUnavailable("native protection unconfirmed")

    def _close(self,p,route,venue,*,now,reason):
        self._reconcile(venue)
        if self._pending(venue):
            raise ExecutionUnavailable("unresolved market order blocks close")
        market,symbol = route["market"],route["symbol"]
        if market=="spot":
            self._clean_stops(venue)
            self._reconcile(venue)
        qty = self._quantity(venue,market)
        if not qty:
            self._clean_stops(venue);return
        rules,quote = venue.instrument(symbol),venue.quote(symbol,now=now)
        amount = rules.round_quantity(abs(qty))
        rules.validate_quantity(amount,quote.bid if qty>0 else quote.ask,reducing=market=="perp")
        snapshot = venue.account_snapshot(now=now)
        self._account_check(venue,market,snapshot)
        if market=="spot" and snapshot.balance(symbol[:-4]).free<amount:
            raise ExecutionUnavailable("managed Spot free balance unavailable after stop cancellation")
        prior = [i for i,u in self._orders(venue) if i.source_id=="close:"+reason]
        close_id = identity(p["campaign_id"],route["entry_id"],market,symbol,"close",str(qty),prior[-1].intent_id if prior else "first")
        order = OrderIntent(intent_id=close_id,account=venue.account_ref,symbol=symbol,market=market,
                            side="SELL" if qty>0 else "BUY",quantity=amount,reduce_only=market=="perp",source_id="close:"+reason,created_at=now)
        coordinator = OrderCoordinator(self.journal,venue)
        update = coordinator.submit(order)
        if update.status in {"NEW","PARTIALLY_FILLED"}:
            update = coordinator.cancel(order)
        self._reconcile(venue)
        if update.status != "FILLED" or self._quantity(venue,market):
            raise ExecutionUnavailable("close incomplete or Spot dust requires operator reconciliation")
        self._clean_stops(venue)

    def flatten(self,*,now):
        with self.journal.lock():
            p = self._portfolio();p.update(paused=True,reason="operator_flatten")
            self._save(p,"operator_portfolio_flatten")
            results = []
            for route in p["routes"].values():
                try:
                    self._close(p,route,self.venue_factory(route["symbol"],route["market"]),now=now,reason="operator")
                    results.append({"symbol":route["symbol"],"market":route["market"],"status":"flat"})
                except (ExecutionUnavailable,ValueError):
                    results.append({"symbol":route["symbol"],"market":route["market"],"status":"reconcile_required"})
            return {"status":"paused","routes":results}

    def cycle(self,*,now):
        with self.journal.lock():
            p = self._portfolio()
            if p.get("enabled") is not True:
                return {"status":"paused","reason":"explicit_activation_required"}
            try:
                return self._cycle(p,now=now)
            except Exception as error:
                if isinstance(error,ExecutionUnavailable) and str(error)=="portfolio_loss_limit":
                    p.update(paused=True,reason="portfolio_loss_limit")
                    self._save(p,"portfolio_loss_limit")
                    for route in p["routes"].values():
                        try:
                            self._close(p,route,self.venue_factory(route["symbol"],route["market"]),now=now,reason="loss_limit")
                        except Exception:
                            pass  # Durable intents/stops remain; status explicitly requires reconciliation.
                    return {"status":"paused","reason":"portfolio_loss_limit","reconciliation_required":True}
                p.update(paused=True,reason="venue_or_state_unavailable_reconcile_required")
                self._save(p,"portfolio_safety_pause")
                # Cover a crash between an uncertain Perp submission and stop ACK.
                # Spot balances cannot prove ownership, so never infer Spot exposure.
                for route in p["routes"].values():
                    if route["market"] == "spot":
                        try:
                            venue = self.venue_factory(route["symbol"],"spot")
                            snapshot = venue.account_snapshot(now=self.clock())
                            self._account_check(venue,"spot",snapshot)
                            if self._quantity(venue,"spot"):
                                self._protect(p,route,venue,snapshot,venue.quote(route["symbol"],now=self.clock()),now=self.clock())
                        except Exception:
                            pass
                        continue
                    if route["market"] != "perp" or not route.get("entry_id"):
                        continue
                    try:
                        venue = self.venue_factory(route["symbol"],"perp")
                        saved = self.journal.order(venue.account_ref,route["entry_id"])
                        snapshot = venue.account_snapshot(now=self.clock())
                        position = snapshot.position(route["symbol"])
                        if (not saved or saved[1].status != "UNKNOWN" or not position
                                or self._quantity(venue,"perp") != 0 or abs(position.quantity)>saved[0].quantity
                                or (position.quantity>0)!=(saved[0].side=="BUY") or not snapshot.can_trade
                                or not snapshot.one_way or not snapshot.single_asset
                                or snapshot.margin_mode.upper()!="ISOLATED" or snapshot.leverage!=3):
                            continue
                        self._protect(p,route,venue,snapshot,venue.quote(route["symbol"],now=self.clock()),
                                      now=self.clock(),observed_perp_quantity=position.quantity)
                    except Exception:
                        pass
                return {"status":"paused","reason":p["reason"]}

    def _cycle(self,p,*,now):
        equity,spot_gross,perp_gross,markets = self._risk(p,now=now)
        plan = DemoAllocation.model_validate(p["allocation"])
        for route in p["routes"].values():
            key = (route["symbol"],route["market"])
            if key not in markets:
                continue
            venue,snapshot,quote = markets[key]
            try:
                self._protect(p,route,venue,snapshot,quote,now=now)
            except (ExecutionUnavailable,ValueError):
                p.update(paused=True,reason="protection_failed")
                self._save(p,"protection_failure")
                self._close(p,route,venue,now=now,reason="protection_failure")
                return {"status":"paused","reason":"protection_failed"}
        if p["paused"]:
            self._save(p,"paused_reconciled")
            return {"status":"paused","reason":p["reason"]}
        results = []
        for route in p["routes"].values():
            if not route["active"]:
                continue
            symbol,market = route["symbol"],route["market"]
            venue,snapshot,quote = markets[(symbol,market)]
            source = self.source_factory(symbol,market)
            if str(source.path) != p["source_database"]:
                raise ValueError("source database changed")
            try:
                rule = source.rule()
                if rule.rule_id != route["rule_id"]:
                    raise ExecutionUnavailable("champion_changed_requires_reactivation")
                source.evaluation(route["evaluation_id"],now=now)
                observation,bar = source.spot_setup(now=now,rule=rule) if market=="spot" else (None,None)
                if market=="spot" and observation.exit and self._quantity(venue,market):
                    self._close(p,route,venue,now=now,reason="donchian_exit")
                    results.append({"symbol":symbol,"market":market,"status":"exit"});continue
                research,decision,rule_id = source.latest_decision(now=self.clock())
            except (ValueError,KeyError,TypeError):
                results.append({"symbol":symbol,"market":market,"status":"waiting_for_eligible_evidence"});continue
            if rule_id != route["rule_id"]:
                continue
            qty = self._quantity(venue,market)
            if qty:
                if market=="perp" and decision.direction.value=="Take Profit":
                    self._close(p,route,venue,now=now,reason="signal_exit")
                continue  # Never pyramid, flip or re-enter in an exit tick.
            # Long public/account scans must not leave a stale quote authorizing entry.
            quote = venue.quote(symbol,now=self.clock())
            snapshot = venue.account_snapshot(now=self.clock())
            self._account_check(venue,market,snapshot)
            if (self.clock()-decision.created_at).total_seconds() > (14400 if market=="spot" else 45):
                continue
            price = Decimal(str(research.features["reference_price" if market=="spot" else "mark_price"]))
            if abs(quote.mark/price-1)>Decimal(".01") or quote.ask/quote.bid-1>Decimal(".001"):
                continue
            if market=="spot" and (route.get("consumed_bar")==bar or int(decision.created_at.timestamp()*1000)<bar):
                continue
            # Gross aggregate projection deliberately does not net opposing Perp coins.
            state = ParentPortfolioState(initial_equity=float(plan.capital),realized_pnl=float(equity-plan.capital),
                spot_quantity=float(spot_gross),spot_entry_price=1 if spot_gross else None,
                perp_quantity=float(perp_gross),perp_entry_price=1 if perp_gross else None,
                mark_price=1,spot_price=1,perp_mark_price=1,day_start_equity=float(p["day_start_equity"]),
                high_water_mark=float(p["high_water_mark"]),entries_paused=False,paper_active=True,updated_at=now)
            auth = (self.gate.spot_entry(state,decision,rule.parameters,donchian_entry=observation.entry,
                                       size_multiplier=observation.size_multiplier,scope=DecisionScope.SPOT_4H) if market=="spot"
                    else self.gate.perp_entry(state,decision,rule.parameters))
            if not auth.allowed or not auth.target_notional:
                continue
            cap = plan.target_cap(symbol,market)
            if market=="spot":
                cap *= Decimal(str(observation.size_multiplier))
            cap = min(cap,abs(Decimal(str(auth.target_notional))), max(Decimal(0),equity*Decimal(".50")-spot_gross-perp_gross))
            if market=="perp":
                cap = min(cap,max(Decimal(0),equity*Decimal(".30")-perp_gross))
            rules = venue.instrument(symbol)
            amount = rules.round_quantity(cap/quote.ask)
            try:
                rules.validate_quantity(amount,quote.ask)
                stop_rules = venue.instrument(symbol,order_type="STOP_LOSS") if market=="spot" else rules
                stop_rules.validate_quantity(stop_rules.round_quantity(amount*Decimal(".999") if market=="spot" else amount),
                                             quote.mark*(1-Decimal(str(route["stop_distance"]))),reducing=market=="perp")
            except ValueError:
                continue
            if market=="spot" and amount*quote.ask*Decimal("1.002")>snapshot.balance("USDT").free:
                continue
            if market=="perp" and amount*quote.ask*Decimal(".335")>snapshot.available_balance:
                continue
            entry_id = identity(p["campaign_id"],symbol,market,decision.decision_id,"entry")
            if self.journal.order(venue.account_ref,entry_id):
                continue
            if (self.clock()-quote.observed_at).total_seconds()>20:
                continue
            route.update(entry_id=entry_id,consumed_bar=bar)
            self._save(p,"portfolio_entry_reserved")
            intent = OrderIntent(intent_id=entry_id,account=venue.account_ref,symbol=symbol,market=market,
                                 side="BUY" if market=="spot" or auth.target_notional>0 else "SELL",quantity=amount,
                                 source_id=decision.decision_id,created_at=self.clock())
            coordinator = OrderCoordinator(self.journal,venue)
            update = coordinator.submit(intent)
            if update.status in {"NEW","PARTIALLY_FILLED"}:
                update = coordinator.cancel(intent)
            self._reconcile(venue)
            confirmed = venue.account_snapshot(now=self.clock())
            observed = confirmed.position(symbol) if market=="perp" else None
            if (market=="perp" and observed and update.status=="UNKNOWN" and self._quantity(venue,market)==0
                    and (observed.quantity>0)==(intent.side=="BUY") and abs(observed.quantity)<=intent.quantity):
                # Reduce-only protection can safely bound confirmed exposure without
                # inventing a fill or adopting inventory. The UNKNOWN entry still pauses.
                self._protect(p,route,venue,confirmed,venue.quote(symbol,now=self.clock()),now=self.clock(),
                              observed_perp_quantity=observed.quantity)
                raise ExecutionUnavailable("uncertain Perp entry protected; reconcile required")
            if self._quantity(venue,market):
                try:
                    self._protect(p,route,venue,confirmed,venue.quote(symbol,now=self.clock()),now=self.clock())
                except (ExecutionUnavailable,ValueError):
                    p.update(paused=True,reason="protection_failed")
                    self._save(p,"protection_failure")
                    self._close(p,route,venue,now=self.clock(),reason="protection_failure")
                    return {"status":"paused","reason":"protection_failed"}
            if self._pending(venue):
                raise ExecutionUnavailable("entry fill unconfirmed")
            # Recompute actual shared exposure after every mutation before another coin.
            equity,spot_gross,perp_gross,markets = self._risk(p,now=self.clock())
            results.append({"symbol":symbol,"market":market,"status":update.status})
        self._save(p,"portfolio_cycle")
        return {"status":"running","equity":str(equity),"spot_gross":str(spot_gross),"perp_gross":str(perp_gross),"routes":results}
