from datetime import timedelta

from intraday.contracts import DecisionScope, PerpRuleParameters, ScopedRuleCandidate
from intraday.outcomes import SignalOutcome
from intraday.perp_bootstrap_lifecycle import start_perp_decision_soak
from intraday.store import IntradayStore
from intraday.replay_v2.funding import fetch_funding_snapshot, save_funding_snapshot
from intraday.replay_v2.lifecycle import replay_gate, start_gate_soak, evaluate_gate_soak, activate_gate_rule
from test_replay_v2_data import NOW, snapshot, record_decision


def phase(store, start, root):
    end = start+timedelta(days=14)
    snapshots, heartbeats = [], []
    signal_times = [start+timedelta(minutes=30+190*i) for i in range(100)]
    for index, at in enumerate(signal_times):
        record_decision(store,at=at,direction="Take Profit" if index % 2 else "Buy")
        with store._connect() as c:
            signal = c.execute("SELECT id FROM signals WHERE timestamp=?",(at.isoformat(),)).fetchone()
        store.record_signal_outcome(SignalOutcome(outcome_id=f"outcome-{signal['id']:020d}",signal_id=signal["id"],
            horizon_sec=900,observed_at=at+timedelta(minutes=15),entry_price=100,exit_price=101,
            forward_return_pct=1,max_upside_pct=1,max_downside_pct=0,directional_return_pct=1,
            sample_count=31,coverage_pct=100))
    for i in range(14*24*120):
        at = start+timedelta(seconds=30*i)
        price = 100
        for index in range(1,100,2):
            if signal_times[index] <= at < signal_times[index]+timedelta(minutes=1):
                price = 100.8
                break
        snap = snapshot(at)
        snap = snap.model_copy(update={"bid":price-.001,"ask":price+.001,
            "features":{**snap.features,"mark_price":price,"reference_price":price}})
        # Snapshot identity/checksum must be regenerated, not changed in place.
        from intraday.contracts import FeatureSnapshot
        snap = FeatureSnapshot.create(symbol=snap.symbol,feature_schema_version="2",event_time=at,built_at=at,
            bid=snap.bid,ask=snap.ask,features=snap.features,freshness=snap.freshness)
        snapshots.append((snap.snapshot_id,at.isoformat(),snap.model_dump_json(),snap.market,snap.symbol))
        heartbeats.append(("DOGEUSDT","perp_intraday","success",0,"scope-price-v2",at.isoformat()))
    with store._connect() as c:
        c.executemany("INSERT OR IGNORE INTO snapshots(id,event_time,payload_json,market,symbol) VALUES (?,?,?,?,?)",snapshots)
        c.executemany("INSERT INTO portfolio_soak_ticks(symbol,scope,status,hard_risk_violation,evidence_version,created_at) VALUES (?,?,?,?,?,?)",heartbeats)
    rows = [{"symbol":"DOGEUSDT","fundingTime":int((start+timedelta(hours=8*i)).timestamp()*1000),
             "fundingRate":".00001","markPrice":"100"} for i in range(42)]
    funding = fetch_funding_snapshot("DOGE",start,end,fetch_json=lambda q:rows,now=end)
    return end, save_funding_snapshot(root,funding)["funding_id"]


def test_perp_full_account_gate_and_new_14_day_validation_use_disjoint_evidence(tmp_path):
    store = IntradayStore(tmp_path/"source.sqlite")
    store.register_asset("DOGE",market="perp",now=NOW)
    rule = ScopedRuleCandidate.create(rule_id="r",parent_rule_id="bootstrap",thesis_id="test",
        symbol="DOGEUSDT",scope=DecisionScope.PERP_INTRADAY,parameters=PerpRuleParameters(),
        created_at=NOW,model_ref="test",prompt_version="test")
    store.register_scoped_rule(rule)
    start_perp_decision_soak(store,rule.rule_id,now=NOW)
    root = tmp_path/"reports"
    end, funding_id = phase(store,NOW,root)
    saved = replay_gate(store,rule.rule_id,now=end,report_dir=root,funding_id=funding_id)
    assert saved.status == "pass", saved.reason_codes
    assert saved.metrics["outcome_count"] == 100
    assert saved.metrics["closed_trades"] >= 6
    start_gate_soak(store,rule.rule_id,evaluation_id=saved.evaluation_id,now=end,report_dir=root)
    early = evaluate_gate_soak(store,rule.rule_id,now=end+timedelta(hours=1),report_dir=root)
    assert early.status == "deferred"
    assert early.metrics["outcome_count"] == 0
    later, new_funding = phase(store,end,root)
    passed = evaluate_gate_soak(store,rule.rule_id,now=later,report_dir=root,funding_id=new_funding)
    assert passed.status == "pass", passed.reason_codes
    assert passed.metrics["outcome_count"] == 100
    assert passed.replay_config.start == end
    promoted = activate_gate_rule(store,rule.rule_id,evaluation_id=passed.evaluation_id,now=later,report_dir=root)
    assert promoted["champion_id"] == rule.rule_id
    assert store.asset_lifecycle(rule.symbol,rule.scope).stage.value == "soak"
