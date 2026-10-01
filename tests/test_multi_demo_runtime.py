from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from intraday.contracts import Direction, JevDecision, PerpRuleParameters, SpotRuleParameters, Regime, RiskLevel
from intraday.execution.allocation import DemoAllocation
from intraday.execution.contracts import (AccountRef, AccountSnapshot, SpotAccountSnapshot, SpotBalance, Position,
                                         OpenOrder, Quote, InstrumentRules, ExecutionFill, OrderUpdate, ExecutionUnavailable)
from intraday.execution.journal import ExecutionJournal
from intraday.execution.multi_runtime import MultiDemoRuntime


NOW = datetime(2026,9,30,3,tzinfo=timezone.utc)
ACCOUNT = AccountRef(venue="binance",environment="demo",account_id="test")


class FakeExchange:
    def __init__(self):
        self.orders = {}
        self.positions = {}
        self.balances = {"USDT":Decimal(10000),"DOGE":Decimal(1000),"ETH":Decimal(500)}
        self.wallet = Decimal(10000)
        self.stop_failure = False
        self.lost_ack = False
        self.hide_query = False
        self.mark = Decimal(100)
        self.leverage = 3
        self.leverages = {}

    def venue(self,symbol,market):
        exchange = self
        class Venue:
            def __init__(self):
                self.symbol = symbol
                self.account_ref = ACCOUNT.model_copy(update={"market":market})
            def check_clock(self,*,now): pass
            def quote(self,symbol,*,now):
                return Quote(bid=exchange.mark-Decimal(".005"),ask=exchange.mark+Decimal(".005"),mark=exchange.mark,observed_at=now)
            def instrument(self,symbol,*,order_type="MARKET"):
                return InstrumentRules(symbol=symbol,quantity_step=".01",min_quantity=".01",max_quantity=10000,min_notional=5,price_tick=".01")
            def funding_since(self,since,*,now): return Decimal(0)
            def account_snapshot(self,*,now):
                opened = tuple(OpenOrder(symbol=i.symbol,client_id=i.intent_id,order_type=i.order_type)
                               for i,u in exchange.orders.values() if i.market==market and not u.terminal)
                if market=="spot":
                    locked = {asset:Decimal(0) for asset in exchange.balances}
                    for i,u in exchange.orders.values():
                        if i.market=="spot" and i.order_type=="STOP_LOSS" and not u.terminal:
                            locked[i.symbol[:-4]] += i.quantity
                    return SpotAccountSnapshot(account=self.account_ref,can_trade=True,balances=tuple(
                        SpotBalance(asset=a,free=q-locked[a],locked=locked[a]) for a,q in exchange.balances.items()),
                        open_orders=opened,observed_at=now)
                return AccountSnapshot(account=self.account_ref,wallet_balance=exchange.wallet,equity=exchange.wallet,
                    available_balance=exchange.wallet,can_trade=True,one_way=True,single_asset=True,margin_mode="ISOLATED",
                    leverage=exchange.leverages.get(symbol, exchange.leverage),positions=tuple(Position(symbol=s,quantity=q,entry_price=100,mark_price=exchange.mark)
                                                           for s,q in exchange.positions.items() if q),open_orders=opened,observed_at=now)
            def submit(self,intent):
                if intent.order_type != "MARKET":
                    update = OrderUpdate(intent_id=intent.intent_id,status="REJECTED" if exchange.stop_failure else "NEW",received_at=NOW)
                else:
                    signed = intent.quantity*(1 if intent.side=="BUY" else -1)
                    fee = intent.quantity*exchange.mark*Decimal(".0005")
                    realized = Decimal(0)
                    if market=="spot":
                        exchange.balances[symbol[:-4]] += signed
                        exchange.balances["USDT"] -= signed*exchange.mark+fee
                    else:
                        if intent.reduce_only:
                            realized = abs(signed)*(exchange.mark-100)*(1 if exchange.positions[symbol]>0 else -1)
                        exchange.positions[symbol] = exchange.positions.get(symbol,Decimal(0))+signed
                        exchange.wallet += realized-fee
                    fill = ExecutionFill(fill_id=symbol+":"+intent.intent_id,quantity=intent.quantity,price=exchange.mark,
                                         commission=fee,commission_asset="USDT",realized_pnl=realized,filled_at=NOW)
                    update = OrderUpdate(intent_id=intent.intent_id,status="FILLED",executed_quantity=intent.quantity,
                                         average_price=exchange.mark,fills=(fill,),received_at=NOW)
                exchange.orders[intent.intent_id] = (intent,update)
                if exchange.lost_ack and intent.order_type=="MARKET": raise ExecutionUnavailable("lost ack")
                return update
            def query(self,intent):
                if exchange.hide_query and intent.order_type=="MARKET": return None
                return exchange.orders.get(intent.intent_id,(None,None))[1]
            def cancel(self,intent):
                i,u = exchange.orders[intent.intent_id]
                if u.terminal: return u
                u = u.model_copy(update={"status":"CANCELED"})
                exchange.orders[intent.intent_id] = (i,u)
                return u
        return Venue()


class Source:
    path = Path("/synthetic/source.sqlite")
    def __init__(self,symbol,market): self.symbol,self.market = symbol,market
    def route(self): return {"venue":"bnb","environment":"demo"}
    def rule(self): return SimpleNamespace(rule_id=self.symbol+"-"+self.market,
                                         parameters=SpotRuleParameters() if self.market=="spot" else PerpRuleParameters())
    def evaluation(self,evaluation_id,*,now):
        if evaluation_id != self.symbol+"-"+self.market+"-pass": raise ValueError("scope evidence mismatch")
    def spot_setup(self,*,now,rule):
        return SimpleNamespace(entry=True,exit=False,size_multiplier=1),int(NOW.timestamp()*1000)-1
    def latest_decision(self,*,now):
        decision = JevDecision(decision_id=self.symbol+"-"+self.market,tick_id="fake-tick",snapshot_id="fake-snapshot",
                               direction=Direction.BUY,direction_confidence=.95,regime=Regime.TRENDING_UP,toxic_flow=.1,
                               entry_quality=4,risk_level=RiskLevel.LOW,model_ref="fixture",created_at=now)
        return SimpleNamespace(features={"mark_price":100,"reference_price":100}),decision,self.rule().rule_id


def runner(tmp_path,plan):
    exchange = FakeExchange()
    runtime = MultiDemoRuntime(ExecutionJournal(tmp_path/"execution.sqlite"),ACCOUNT,exchange.venue,Source,clock=lambda:NOW)
    runtime.configure(plan,now=NOW)
    return runtime,exchange


@pytest.mark.parametrize("leverage", [1, 2, 5, 10])
def test_configured_leverage_changes_margin_not_notional_allocation(tmp_path, leverage):
    exchange = FakeExchange()
    exchange.leverage = leverage
    journal = ExecutionJournal(tmp_path/"execution.sqlite")
    item = {"id":"fixture-setting", "account":ACCOUNT.key, "symbol":"ETHUSDT", "status":"verified"}
    journal.save_settings_request(item, now=NOW, verified_leverage=leverage)
    runtime = MultiDemoRuntime(journal, ACCOUNT, exchange.venue, Source, clock=lambda:NOW)
    runtime.configure(DemoAllocation(capital=1000, perp_weights={"ETHUSDT":1}), now=NOW)
    runtime.activate("ETHUSDT", "perp", evaluation_id="ETHUSDT-perp-pass", now=NOW)
    report = runtime.cycle(now=NOW)
    assert report["status"] == "running", report
    notional = abs(exchange.positions.get("ETHUSDT", Decimal(0))) * exchange.mark
    assert notional > 0
    assert notional <= 200
    assert notional / leverage <= 100


def test_leverage_drift_keeps_protection_and_allows_flatten(tmp_path):
    runtime, exchange = runner(tmp_path, DemoAllocation(capital=1000, perp_weights={"ETHUSDT":1}))
    runtime.activate("ETHUSDT", "perp", evaluation_id="ETHUSDT-perp-pass", now=NOW)
    assert runtime.cycle(now=NOW)["status"] == "running"
    exchange.leverage = 5
    report = runtime.cycle(now=NOW)
    assert report["status"] == "paused"
    assert report["reason"] == "perp_settings_drift"
    assert any(i.order_type == "STOP_MARKET" and not u.terminal for i,u in exchange.orders.values())
    assert runtime.flatten(now=NOW)["routes"][0]["status"] == "flat"
    assert not exchange.positions["ETHUSDT"]


def test_mixed_pair_leverage_uses_sum_of_margins_not_one_divisor(tmp_path):
    exchange = FakeExchange()
    exchange.leverages = {"ETHUSDT":1, "SOLUSDT":5}
    journal = ExecutionJournal(tmp_path/"execution.sqlite")
    for symbol, leverage in exchange.leverages.items():
        journal.save_settings_request({"id":"fixture-"+symbol,"account":ACCOUNT.key,
            "symbol":symbol,"status":"verified"}, now=NOW, verified_leverage=leverage)
    runtime = MultiDemoRuntime(journal, ACCOUNT, exchange.venue, Source, clock=lambda:NOW)
    runtime.configure(DemoAllocation(capital=1000, perp_weights={"ETHUSDT":".4", "SOLUSDT":".6"}), now=NOW)
    for symbol in exchange.leverages:
        runtime.activate(symbol, "perp", evaluation_id=symbol+"-perp-pass", now=NOW)
    report = runtime.cycle(now=NOW)
    assert report["status"] == "running", report
    assert all(exchange.positions.get(s, 0) > 0 for s in exchange.leverages)
    margin = sum(abs(q)*exchange.mark/exchange.leverages[s] for s,q in exchange.positions.items())
    assert margin <= Decimal(report["equity"])*Decimal(".10")


def test_reported_initial_margin_is_respected(tmp_path):
    runtime, exchange = runner(tmp_path, DemoAllocation(capital=1000, perp_weights={"ETHUSDT":1}))
    venue = exchange.venue("ETHUSDT", "perp")
    snapshot = venue.account_snapshot(now=NOW).model_copy(update={"positions":(
        Position(symbol="ETHUSDT", quantity=1, entry_price=100, mark_price=100, initial_margin=80),)})
    quote = venue.quote("ETHUSDT", now=NOW)
    assert runtime._perp_margin({("ETHUSDT","perp"):(venue,snapshot,quote)}) == 80


def test_shared_multi_coin_caps_and_spot_inventory_does_not_adopt_seeded_balance(tmp_path):
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,spot_weights={"DOGEUSDT":".5","ETHUSDT":".5"},perp_weights={"ETHUSDT":1}))
    assert runtime.cycle(now=NOW)["reason"] == "explicit_activation_required"
    assert not exchange.orders
    for symbol,market in [("DOGEUSDT","spot"),("ETHUSDT","spot"),("ETHUSDT","perp")]:
        runtime.activate(symbol,market,evaluation_id=symbol+"-"+market+"-pass",now=NOW)
    assert not exchange.orders
    result = runtime.cycle(now=NOW)
    assert result["status"] == "running",result
    assert Decimal(result["spot_gross"]) <= 300 and Decimal(result["perp_gross"]) <= 200
    owned = runtime.journal.spot_inventory(ACCOUNT.model_copy(update={"market":"spot"}),"DOGEUSDT")
    assert 0 < owned["quantity"] < 2
    assert exchange.balances["DOGE"] == 1000+owned["quantity"]
    spot_stop = next(i for i,u in exchange.orders.values() if i.symbol=="DOGEUSDT" and i.order_type=="STOP_LOSS")
    assert spot_stop.stop_price == Decimal("90")  # 10% below actual entry, not fee-inclusive accounting cost.
    count = len(exchange.orders)
    assert runtime.cycle(now=NOW)["status"] == "running"
    assert len(exchange.orders) == count
    closed = runtime.flatten(now=NOW)
    assert all(r["status"]=="flat" for r in closed["routes"]),closed
    assert exchange.balances["DOGE"] == 1000 and exchange.balances["ETH"] == 500
    assert all(u.terminal for i,u in exchange.orders.values())


def test_failed_native_spot_stop_closes_only_owned_inventory(tmp_path):
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,spot_weights={"DOGEUSDT":1}))
    runtime.activate("DOGEUSDT","spot",evaluation_id="DOGEUSDT-spot-pass",now=NOW)
    exchange.stop_failure = True
    assert runtime.cycle(now=NOW)["status"] == "paused"
    assert exchange.balances["DOGE"] == 1000
    sells = [i for i,u in exchange.orders.values() if i.side=="SELL" and i.order_type=="MARKET"]
    assert len(sells)==1 and sells[0].quantity<4


def test_wrong_scope_evidence_configuration_mismatch_and_manual_cash_reset_block_trade(tmp_path):
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,perp_weights={"ETHUSDT":1}))
    with pytest.raises(ValueError): runtime.activate("ETHUSDT","perp",evaluation_id="BTCUSDT-perp-pass",now=NOW)
    exchange.leverage = 5
    with pytest.raises(ValueError): runtime.activate("ETHUSDT","perp",evaluation_id="ETHUSDT-perp-pass",now=NOW)
    exchange.leverage = 3
    runtime.activate("ETHUSDT","perp",evaluation_id="ETHUSDT-perp-pass",now=NOW)
    exchange.wallet += 100
    assert runtime.cycle(now=NOW)["status"] == "paused"
    assert not exchange.orders


def test_unknown_perp_entry_is_not_repeated_and_observed_exposure_gets_reducing_stop(tmp_path):
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,perp_weights={"ETHUSDT":1}))
    runtime.activate("ETHUSDT","perp",evaluation_id="ETHUSDT-perp-pass",now=NOW)
    exchange.lost_ack = True;exchange.hide_query = True
    assert runtime.cycle(now=NOW)["status"]=="paused"
    entries = [i for i,u in exchange.orders.values() if i.order_type=="MARKET"]
    assert len(entries)==1
    stops = [i for i,u in exchange.orders.values() if i.order_type=="STOP_MARKET"]
    assert len(stops)==1 and stops[0].reduce_only and stops[0].quantity==exchange.positions["ETHUSDT"]
    assert runtime.cycle(now=NOW)["status"]=="paused"
    assert len([i for i,u in exchange.orders.values() if i.order_type=="MARKET"])==1


def test_portfolio_loss_limit_flattens_managed_spot_without_touching_seeded_coins(tmp_path):
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,spot_weights={"DOGEUSDT":1}))
    runtime.activate("DOGEUSDT","spot",evaluation_id="DOGEUSDT-spot-pass",now=NOW)
    assert runtime.cycle(now=NOW)["status"]=="running"
    exchange.mark = Decimal(94)  # Less than the new 10% stop, but portfolio daily loss exceeds 1.5%.
    assert runtime.cycle(now=NOW)["reason"]=="portfolio_loss_limit"
    assert exchange.balances["DOGE"]==1000


def test_restart_preserves_owned_inventory_and_does_not_reset_watermarks_or_capital(tmp_path):
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,spot_weights={"DOGEUSDT":1}))
    runtime.activate("DOGEUSDT","spot",evaluation_id="DOGEUSDT-spot-pass",now=NOW)
    runtime.cycle(now=NOW)
    resumed = MultiDemoRuntime(ExecutionJournal(runtime.journal.path),ACCOUNT,exchange.venue,Source,clock=lambda:NOW)
    count = len(exchange.orders)
    assert resumed.cycle(now=NOW)["status"]=="running"
    assert len(exchange.orders)==count
    resumed.pause(now=NOW)
    with pytest.raises(ValueError,match="flatten"):
        resumed.configure(DemoAllocation(capital=1000,spot_weights={"DOGEUSDT":".5"}),now=NOW)
    resumed.flatten(now=NOW)
    with pytest.raises(ValueError,match="capital"):
        resumed.configure(DemoAllocation(capital=2000,spot_weights={"DOGEUSDT":1}),now=NOW)


def test_uncertain_spot_fill_pauses_without_adopting_or_selling_seeded_inventory(tmp_path):
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,spot_weights={"DOGEUSDT":1}))
    runtime.activate("DOGEUSDT","spot",evaluation_id="DOGEUSDT-spot-pass",now=NOW)
    exchange.lost_ack=True;exchange.hide_query=True
    assert runtime.cycle(now=NOW)["status"]=="paused"
    assert runtime.journal.spot_inventory(ACCOUNT.model_copy(update={"market":"spot"}),"DOGEUSDT")["quantity"]==0
    assert len(exchange.orders)==1
    assert runtime.cycle(now=NOW)["status"]=="paused" and len(exchange.orders)==1
    exchange.hide_query=False;exchange.lost_ack=False
    assert runtime.cycle(now=NOW)["status"]=="paused"
    assert any(i.order_type=="STOP_LOSS" for i,u in exchange.orders.values())
    runtime.flatten(now=NOW)
    assert exchange.balances["DOGE"]==1000


def test_legacy_and_new_runtime_cannot_activate_the_same_journal(tmp_path):
    from intraday.execution.runtime import DemoRuntime
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,perp_weights={"ETHUSDT":1}))
    legacy = DemoRuntime(runtime.journal,SimpleNamespace(account_ref=ACCOUNT),None)
    with pytest.raises(ValueError,match="multi-route"):
        legacy.activate(evaluation_id="any",capital=Decimal(1000),now=NOW)


def test_base_fee_dust_is_preserved_and_never_topped_up_from_seeded_inventory(tmp_path):
    runtime,exchange = runner(tmp_path,DemoAllocation(capital=1000,spot_weights={"DOGEUSDT":1}))
    runtime.activate("DOGEUSDT","spot",evaluation_id="DOGEUSDT-spot-pass",now=NOW)
    original=exchange.venue
    def venue_with_base_fee(symbol,market):
        venue=original(symbol,market);submit=venue.submit
        def submit_fee(intent):
            update=submit(intent)
            if market=="spot" and intent.order_type=="MARKET" and intent.side=="BUY":
                fill=update.fills[0]
                fee=intent.quantity*Decimal(".001")
                exchange.balances["DOGE"]-=fee
                exchange.balances["USDT"]+=fill.commission
                update=update.model_copy(update={"fills":(fill.model_copy(update={"commission":fee,"commission_asset":"DOGE"}),)})
                exchange.orders[intent.intent_id]=(intent,update)
            return update
        venue.submit=submit_fee
        return venue
    runtime.venue_factory=venue_with_base_fee
    assert runtime.cycle(now=NOW)["status"]=="running"
    result=runtime.flatten(now=NOW)
    assert result["routes"][0]["status"]=="reconcile_required"
    owned=runtime.journal.spot_inventory(ACCOUNT.model_copy(update={"market":"spot"}),"DOGEUSDT")
    assert 0 < owned["quantity"] < Decimal(".01")
    assert exchange.balances["DOGE"]==1000+owned["quantity"]
