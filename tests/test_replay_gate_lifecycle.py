from datetime import timedelta

import pytest

from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
from intraday.store import IntradayStore
from intraday.replay_v2.gate_repository import GateRepository
from intraday.replay_v2.lifecycle import replay_gate, start_gate_soak, evaluate_gate_soak, activate_gate_rule
from test_spot_4h_lifecycle import NOW, trend_rows


def setup(tmp_path):
    store = IntradayStore(tmp_path/"source.sqlite")
    rule = ScopedRuleCandidate.create(rule_id="r", parent_rule_id="bootstrap", thesis_id="test",
        symbol="ETHUSDT", scope=DecisionScope.SPOT_4H, parameters=SpotRuleParameters(),
        created_at=NOW, model_ref="test", prompt_version="test")
    store.register_scoped_rule(rule)
    store.record_asset_candles(rule.symbol,"4h",trend_rows())
    return store, rule, tmp_path/"reports"


def test_real_spot_engine_gate_and_operator_validation_preserve_v1_evidence(tmp_path):
    store, rule, root = setup(tmp_path)
    counts = store.counts()
    evaluation = replay_gate(store,rule.rule_id,now=NOW,report_dir=root)
    assert evaluation.status == "pass", evaluation.reason_codes
    assert evaluation.metrics["closed_trades"] >= 6
    assert evaluation.run_id is not None
    assert store.scoped_rule_status(rule.rule_id) == "queued"
    assert store.latest_scoped_rule_evaluation(rule.rule_id,kind="replay") is None
    assert store.counts() == counts
    campaign = start_gate_soak(store,rule.rule_id,evaluation_id=evaluation.evaluation_id,now=NOW,report_dir=root)
    assert campaign["started_at"] == NOW.isoformat()
    early = evaluate_gate_soak(store,rule.rule_id,now=NOW+timedelta(hours=1),report_dir=root)
    assert early.status == "deferred"
    assert early.campaign_id == campaign["campaign_id"]
    with pytest.raises(ValueError,match="passing"):
        activate_gate_rule(store,rule.rule_id,evaluation_id=early.evaluation_id,now=NOW+timedelta(hours=1),report_dir=root)
    with pytest.raises(ValueError,match="active validation"):
        replay_gate(store,rule.rule_id,now=NOW+timedelta(hours=1),report_dir=root)


def test_tampered_report_cannot_start_soak(tmp_path):
    store, rule, root = setup(tmp_path)
    evaluation = replay_gate(store,rule.rule_id,now=NOW,report_dir=root)
    (root/evaluation.run_id/"events.jsonl").write_text("{}\n")
    with pytest.raises(ValueError,match="checksum"):
        start_gate_soak(store,rule.rule_id,evaluation_id=evaluation.evaluation_id,now=NOW,report_dir=root)
    assert GateRepository(store).current(rule.symbol,rule.scope) is None
    assert store.scoped_rule_status(rule.rule_id) == "queued"


def test_perp_missing_real_quotes_decisions_and_funding_defers_without_reset(tmp_path):
    from intraday.perp_bootstrap_lifecycle import bootstrap_perp_rule, start_perp_decision_soak
    store = IntradayStore(tmp_path/"source.sqlite")
    rule = bootstrap_perp_rule(store,"SOLUSDT",now=NOW-timedelta(days=20))
    start_perp_decision_soak(store,rule.rule_id,now=NOW-timedelta(days=20))
    anchor = store.scoped_rule_registry(rule.scope,symbol=rule.symbol)["updated_at"]
    evaluation = replay_gate(store,rule.rule_id,now=NOW,report_dir=tmp_path/"reports")
    assert evaluation.status == "deferred"
    assert "funding_coverage_incomplete" in evaluation.reason_codes
    assert store.scoped_rule_registry(rule.scope,symbol=rule.symbol)["updated_at"] == anchor


def test_post_gate_spot_promotes_only_with_fresh_samples_and_execution_requires_v2_id(tmp_path):
    from intraday.execution.scoped_source import ScopedEvidenceSource
    store, rule, root = setup(tmp_path)
    saved = replay_gate(store,rule.rule_id,now=NOW,report_dir=root)
    start_gate_soak(store,rule.rule_id,evaluation_id=saved.evaluation_id,now=NOW,report_dir=root)
    width = 14_400_000
    first = int(NOW.timestamp()*1000)//width*width
    store.record_asset_candles(rule.symbol,"4h",[
        [first+i*width,str(100+i),str(101+i),str(99+i),str(100+i),"10",first+(i+1)*width-1]
        for i in range(84)])
    for i in range(84):
        at = NOW+timedelta(hours=4*i)
        store.record_portfolio_soak_tick(symbol=rule.symbol,scope=rule.scope,status="success",created_at=at)
        if i not in {12,24,36,48,60,72}:
            continue
        signal_id = store.record_journal_signal(decision_id=f"post-gate-{i}",timestamp=at,
            symbol=rule.symbol,scope=rule.scope,state_snapshot='{"symbol":"ETHUSDT"}',
            raw_signals={"price":100+i},jev_answers={"direction":{"choice":"Buy","probabilities":{"Buy":.9}}},
            gate_passed=False,gate_reason="asset_soak_observation_only",rules_version=rule.rule_id,
            llm_thesis=None,market="binance_spot",feature_schema_version="3")
        store.record_scoped_rule_soak_tick(candidate_id=rule.rule_id,signal_id=signal_id,
            champion_allowed=False,challenger_allowed=True,champion_score=0,challenger_score=0,created_at=at)
    later = NOW+timedelta(days=14)
    outcome = evaluate_gate_soak(store,rule.rule_id,now=later,report_dir=root)
    assert outcome.status == "pass", outcome.reason_codes
    assert outcome.metrics["matured_setups"] == 6
    result = activate_gate_rule(store,rule.rule_id,evaluation_id=outcome.evaluation_id,now=later,report_dir=root)
    assert result["champion_id"] == rule.rule_id
    assert store.asset_lifecycle(rule.symbol,rule.scope).stage.value == "soak"
    with store._connect() as c:
        c.execute("INSERT INTO asset_venue_routes VALUES (?,?,?,?,?,?,?)",
                  (rule.symbol,"spot","bnb","demo",rule.symbol,"fixture",later.isoformat()))
    source = ScopedEvidenceSource(store.database,symbol=rule.symbol,market="spot")
    assert source.evaluation(outcome.evaluation_id,now=later).engine_version == "gate-v2.1"
    with pytest.raises(ValueError):
        source.evaluation(saved.evaluation_id,now=later)
