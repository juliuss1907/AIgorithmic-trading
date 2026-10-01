import hashlib
import json
from decimal import Decimal

import pytest

from intraday.execution.allocation import DemoAllocation
from intraday.execution.scoped_source import ScopedEvidenceSource
from intraday.store import IntradayStore
from intraday.contracts import (DecisionScope, SpotRuleParameters, ScopedRuleCandidate, FeatureSnapshot,
                                ProviderProfile, ProviderRole, ProviderKind)
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation
from intraday.provider_client import HttpResponse
from intraday.provider_profiles import ProviderCredential
from intraday.providers import JevDecisionProvider
from intraday.portfolio_soak import run_soak_cycle
from test_execution_source import NOW, source_fixture


def test_allocation_keeps_manual_weights_reserve_and_shared_caps():
    plan = DemoAllocation(capital=1000, spot_weights={"ETHUSDT": ".5", "DOGEUSDT": ".25"}, perp_weights={"ETHUSDT": 1})
    assert plan.target_cap("ETHUSDT", "spot") == 150
    assert plan.target_cap("DOGEUSDT", "spot") == 75
    assert plan.target_cap("ETHUSDT", "perp") == 200
    with pytest.raises(ValueError):
        DemoAllocation(capital=1000, spot_weights={"ETHUSDT": ".8", "DOGEUSDT": ".3"})
    with pytest.raises(ValueError):
        DemoAllocation(capital=1000, perp_weights={"ETHUSDT": Decimal("NaN")})
    with pytest.raises(ValueError):
        plan.target_cap("BTCUSDT", "spot")


def test_scoped_source_requires_operator_route_and_does_not_reuse_btc_pass_for_eth(tmp_path):
    path = source_fixture(tmp_path)
    store = IntradayStore(path)
    with store._connect() as c:
        c.execute("INSERT INTO asset_venue_routes VALUES (?,?,?,?,?,?,?)", ("BTCUSDT","perp","bnb","demo","BTCUSDT","test",NOW.isoformat()))
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    btc = ScopedEvidenceSource(path,symbol="BTC",market="perp")
    assert btc.evaluation("0123456789abcdef",now=NOW).status == "pass"
    assert btc.latest_decision(now=NOW)[0].symbol == "BTCUSDT"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    with pytest.raises(ValueError, match="route"):
        ScopedEvidenceSource(path,symbol="ETH",market="perp").evaluation("0123456789abcdef",now=NOW)
    with pytest.raises(ValueError, match="route"):
        ScopedEvidenceSource(path,symbol="BTC",market="spot").rule()


def test_eth_spot_primary_provenance_and_exact_scope_evaluations(tmp_path):
    path=source_fixture(tmp_path);store=IntradayStore(path)
    rule=ScopedRuleCandidate.create(rule_id="eth-spot-rule",parent_rule_id="seed",thesis_id="seed",
        scope=DecisionScope.SPOT_4H,symbol="ETHUSDT",parameters=SpotRuleParameters(),created_at=NOW,
        model_ref="operator",prompt_version="fixture")
    store.register_scoped_rule(rule)
    store.activate_scoped_champion(rule.scope,rule.rule_id,symbol="ETHUSDT",now=NOW)
    with store._connect() as c:
        c.execute("INSERT INTO asset_venue_routes VALUES (?,?,?,?,?,?,?)",("ETHUSDT","spot","bnb","demo","ETHUSDT","test",NOW.isoformat()))
    profile=ProviderProfile.create(profile_id="jev-test",role=ProviderRole.JEV,kind=ProviderKind.OPENROUTER_DECISIONS,
        base_url="https://openrouter.ai/api/alpha/decisions",model="typesafe/jev-1.13",credential_version="v1",created_at=NOW,updated_at=NOW)
    answers={"direction":{"choice":"Buy","probabilities":{"Buy":.95}},"regime":{"choice":"Trending Up"},
             "risk_level":{"choice":"Low"},"entry_quality":{"score":3},"toxic_flow":{"noul":.1}}
    provider=JevDecisionProvider(ProviderCredential(profile,"fixture-only"),store=store,
        transport=lambda **_:HttpResponse(200,{},json.dumps({"answers":answers}).encode()))
    snapshot=FeatureSnapshot.create(symbol="ETHUSDT",market="binance_spot",timeframe="4h",feature_schema_version="3",
        event_time=NOW,built_at=NOW,bid=99.99,ask=100.01,features={"reference_price":100,"price":100},
        freshness={n:True for n in ("candles_4h","candles_8h","candles_1d","order_book")})
    run_soak_cycle(store,provider,snapshot,now=NOW,scopes=(DecisionScope.SPOT_4H,))
    evaluations=[]
    for kind in ("replay","soak"):
        evaluation=ScopedRuleEvaluation.create(candidate_id=rule.rule_id,scope=rule.scope,symbol="ETHUSDT",kind=kind,
            status="pass",evaluated_at=NOW,sample_count=100,coverage=1,champion_score=0,challenger_score=1)
        store.record_scoped_rule_evaluation(evaluation);evaluations.append(evaluation)
    source=ScopedEvidenceSource(path,symbol="ETH",market="spot")
    original=hashlib.sha256(path.read_bytes()).hexdigest()
    assert source.evaluation(evaluations[-1].evaluation_id,now=NOW).scope==DecisionScope.SPOT_4H
    assert source.latest_decision(now=NOW)[0].feature_schema_version=="3"
    with pytest.raises(ValueError): source.evaluation("0123456789abcdef",now=NOW)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==original
