import math

import pytest
from pydantic import ValidationError

from intraday.contracts import PerpRuleParameters, RuleParameters, SpotRuleParameters, Regime, RiskLevel
from intraday.replay_v2.confidence_contracts import ConfidenceProposal
from intraday.scoped_rule_lifecycle import rule_allows_answers
from test_replay_v2_perp import inputs, quote, decision


@pytest.mark.parametrize("rate", [.69, .70, .732, .85, .986, 1.0])
def test_proposal_accepts_finite_rates_without_rounding(rate):
    item = ConfidenceProposal(status="proposed", confidence_threshold=rate,
                              rationale="Evidence supports this per-coin threshold.")
    assert item.confidence_threshold == rate
    assert PerpRuleParameters(confidence_threshold=rate).confidence_threshold == rate


@pytest.mark.parametrize("rate", [.6899, -.1, 1.0001, math.nan, math.inf, -math.inf])
def test_proposal_rejects_invalid_rates(rate):
    with pytest.raises(ValidationError):
        ConfidenceProposal(status="proposed", confidence_threshold=rate,
                           rationale="Evidence supports this threshold.")
    with pytest.raises(ValidationError):
        PerpRuleParameters(confidence_threshold=rate)


def test_insufficient_evidence_never_supplies_a_fallback_threshold():
    item = ConfidenceProposal(status="insufficient_data", rationale="No independent closed trades yet.")
    assert item.confidence_threshold is None
    with pytest.raises(ValidationError):
        ConfidenceProposal(status="proposed", rationale="No threshold is specified here.")
    with pytest.raises(ValidationError):
        ConfidenceProposal(status="insufficient_data", confidence_threshold=.75,
                           rationale="No independent closed trades yet.")


def test_low_perp_rate_does_not_change_spot_or_legacy_policy():
    assert PerpRuleParameters().confidence_threshold == .85
    with pytest.raises(ValidationError):
        SpotRuleParameters(jev_confidence_threshold=.69)
    with pytest.raises(ValidationError):
        RuleParameters(confidence_threshold=.69)


def test_archived_perp_rule_roundtrip_keeps_values_and_hash():
    from intraday.contracts import ScopedRuleCandidate
    from intraday.replay_v2.study import variant_rule
    _, data = inputs([quote(0),quote(10)],[decision()])
    archived_json = data.rule.model_dump_json()
    loaded = ScopedRuleCandidate.model_validate_json(archived_json)
    assert loaded.model_dump_json() == archived_json
    assert loaded.content_hash == data.rule.content_hash
    variant = variant_rule(loaded,{"confidence_threshold":.732},now=loaded.created_at)
    assert variant.content_hash != loaded.content_hash
    assert loaded.model_dump_json() == archived_json


def test_tolerance_is_not_subtracted_from_the_actual_filter():
    from intraday.contracts import ScopedRuleCandidate
    config, data = inputs([quote(0), quote(10)], [decision()])
    payload = data.rule.model_dump(exclude={"content_hash"})
    payload["parameters"] = PerpRuleParameters(confidence_threshold=.732)
    rule = ScopedRuleCandidate.create(**payload)
    answers = {"direction": {"choice": "Buy", "probabilities": {"Buy": .7319}},
               "regime": {"choice": Regime.TRENDING_UP.value}, "risk_level": {"choice": RiskLevel.LOW.value},
               "toxic_flow": {"noul": .1}, "entry_quality": {"score": 3}}
    assert not rule_allows_answers(rule, answers)
    answers["direction"]["probabilities"]["Buy"] = .99
    assert rule_allows_answers(rule, answers)
    assert data.rule.parameters.confidence_threshold == .85


@pytest.mark.parametrize("status,rate", [("proposed",.986),("insufficient_data",None)])
def test_structured_provider_accepts_confidence_contract_without_retries(tmp_path,status,rate):
    import json
    from intraday.llm_pipeline import StructuredLLMClient
    from intraday.provider_client import HttpResponse
    from intraday.provider_profiles import ProviderCredential
    from intraday.store import IntradayStore
    from test_intraday_llm_pipeline import llm_profile, NOW
    requests = []
    response = ConfidenceProposal(status=status,confidence_threshold=rate,
                                  rationale="Training evidence determines the proposal outcome.")
    def transport(**request):
        requests.append(json.loads(request["body"]))
        return HttpResponse(200,{},json.dumps({"choices":[{"message":{
            "content":response.model_dump_json()}}]}).encode())
    store = IntradayStore(tmp_path/"research.sqlite")
    profile = llm_profile()
    store.sync_provider_profile(profile)
    client = StructuredLLMClient(ProviderCredential(profile,"fixture-only"),
                                 store=store,transport=transport)
    parsed = client.complete(workflow="perp_confidence_review_ETHUSDT",
        response_model=ConfidenceProposal,system_prompt="Training only.",input_payload={},now=NOW)
    assert parsed == response and len(requests) == 1
    schema = requests[0]["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
