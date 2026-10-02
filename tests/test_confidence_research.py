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
