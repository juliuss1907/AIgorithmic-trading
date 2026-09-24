from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import (
    DecisionScope,
    PerpRuleParameters,
    ScopedRuleCandidate,
)
from intraday.scoped_rule_lifecycle import (
    ScopedRuleEvaluation,
    activate_scoped_rule,
    evaluate_scoped_replay,
    start_scoped_rule_soak,
)
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def rule(rule_id, parent, *, confidence=0.85):
    return ScopedRuleCandidate.create(
        rule_id=rule_id, parent_rule_id=parent, thesis_id="thesis-1",
        scope=DecisionScope.PERP_INTRADAY,
        parameters=PerpRuleParameters(confidence_threshold=confidence),
        created_at=NOW, model_ref="test/llm", prompt_version="analysis-v2",
        rationale="Use bounded entry filters without changing immutable hard risk.",
    )


def setup_rules(store):
    champion = rule("perp-champion", "root")
    candidate = rule("perp-candidate", champion.rule_id, confidence=0.9)
    store.register_scoped_rule(champion, status="champion")
    store.activate_scoped_champion(champion.scope, champion.rule_id, now=NOW)
    store.register_scoped_rule(candidate, status="queued")
    return champion, candidate


def test_scoped_replay_defers_without_enough_immutable_evidence(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    _, candidate = setup_rules(store)

    evaluation = evaluate_scoped_replay(store, candidate.rule_id, now=NOW)

    assert evaluation.status == "deferred"
    assert "minimum_100_outcomes" in evaluation.reason_codes
    assert store.scoped_rule_status(candidate.rule_id) == "queued"


def test_scoped_soak_and_activation_are_manual_and_evaluation_gated(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    champion, candidate = setup_rules(store)
    replay = ScopedRuleEvaluation.create(
        candidate_id=candidate.rule_id, scope=candidate.scope, kind="replay",
        status="pass", evaluated_at=NOW, started_at=None,
        sample_count=120, coverage=1, champion_score=0.5,
        challenger_score=0.55,
    )
    store.record_scoped_rule_evaluation(replay)

    start_scoped_rule_soak(store, candidate.rule_id, now=NOW)
    assert store.scoped_rule_registry(candidate.scope)["challenger_id"] == candidate.rule_id
    assert store.load_active_scoped_rule(candidate.scope).rule_id == champion.rule_id

    promotion = ScopedRuleEvaluation.create(
        candidate_id=candidate.rule_id, scope=candidate.scope, kind="soak",
        status="pass", evaluated_at=NOW + timedelta(hours=72), started_at=NOW,
        sample_count=100, coverage=1, champion_score=0.5,
        challenger_score=0.55,
    )
    store.record_scoped_rule_evaluation(promotion)

    with pytest.raises(ValueError, match="exact passing soak"):
        activate_scoped_rule(store, candidate.rule_id, evaluation_id="wrong-id", now=promotion.evaluated_at)
    activated = activate_scoped_rule(
        store, candidate.rule_id, evaluation_id=promotion.evaluation_id,
        now=promotion.evaluated_at,
    )
    assert activated["champion_id"] == candidate.rule_id
    assert activated["rollback_id"] == champion.rule_id
