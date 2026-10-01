from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation
from intraday.store import IntradayStore
from intraday.replay_v2.contracts import ReplayConfig, binance_gate_profile
from intraday.replay_v2.gates import GateEvaluation, profile_fingerprint
from intraday.replay_v2.gate_repository import GateRepository


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def setup(tmp_path):
    store = IntradayStore(tmp_path/"source.sqlite")
    store.register_asset("DOGE", market="spot", now=NOW)
    rule = ScopedRuleCandidate.create(rule_id="r", parent_rule_id="bootstrap", thesis_id="test",
        scope=DecisionScope.SPOT_4H, symbol="DOGEUSDT", parameters=SpotRuleParameters(),
        created_at=NOW-timedelta(days=1), model_ref="test", prompt_version="test")
    store.register_scoped_rule(rule)
    return store, rule, GateRepository(store)


def evaluation(rule, *, at=NOW, status="pass", **extra):
    config = ReplayConfig(symbol=rule.symbol, market="spot", rule_id=rule.rule_id,
        start=NOW-timedelta(days=180), end=NOW, profile=binance_gate_profile())
    return GateEvaluation.create(candidate_id=rule.rule_id, rule_content_hash=rule.content_hash,
        kind="replay", status=status, evaluated_at=at, replay_config=config,
        profile_hash=profile_fingerprint(config.profile), **extra)


def test_extension_is_optional_to_readers_and_never_migrates_during_read(tmp_path):
    store, rule, _ = setup(tmp_path)
    before = store.database.read_bytes()
    reader = GateRepository(IntradayStore(store.database, read_only=True))
    assert reader.latest(rule.rule_id) is None
    assert reader.current(rule.symbol, rule.scope) is None
    assert store.database.read_bytes() == before


def test_v1_rejection_is_preserved_when_operator_starts_new_v2_campaign(tmp_path):
    store, rule, repository = setup(tmp_path)
    legacy = ScopedRuleEvaluation.create(candidate_id=rule.rule_id, symbol=rule.symbol, scope=rule.scope,
        kind="replay", status="reject", evaluated_at=NOW-timedelta(hours=1), started_at=None,
        sample_count=14, coverage=1, champion_score=0, challenger_score=-1)
    store.record_scoped_rule_evaluation(legacy)
    store.update_scoped_rule_status(rule.rule_id, expected="queued", status="rejected")
    saved = evaluation(rule)
    repository.record(saved)
    repository.record(saved)
    assert store.scoped_rule_status(rule.rule_id) == "rejected"
    campaign = repository.start(saved.evaluation_id, now=NOW)
    assert campaign["replay_evaluation_id"] == saved.evaluation_id
    assert store.scoped_rule_status(rule.rule_id) == "challenger"
    assert store.scoped_rule_evaluation(legacy.evaluation_id).status == "reject"
    assert store.latest_scoped_rule_evaluation(rule.rule_id, kind="replay") == legacy
    with repository.store._connect() as connection:
        with pytest.raises(Exception, match="immutable"):
            connection.execute("DELETE FROM replay_gate_evaluations")


def test_old_pass_wrong_profile_and_future_evaluations_cannot_start(tmp_path):
    _, rule, repository = setup(tmp_path)
    old = evaluation(rule)
    repository.record(old)
    repository.record(evaluation(rule, at=NOW+timedelta(seconds=1), status="reject"))
    with pytest.raises(ValueError, match="latest"):
        repository.start(old.evaluation_id, now=NOW+timedelta(seconds=2))
    future = evaluation(rule, at=NOW+timedelta(hours=1))
    repository.record(future)
    with pytest.raises(ValueError, match="future"):
        repository.start(future.evaluation_id, now=NOW)


def test_validation_binding_is_immutable_and_cannot_be_restarted(tmp_path):
    _, rule, repository = setup(tmp_path)
    saved = evaluation(rule)
    repository.record(saved)
    campaign = repository.start(saved.evaluation_id, now=NOW)
    with pytest.raises(ValueError, match="active"):
        repository.start(saved.evaluation_id, now=NOW+timedelta(days=1))
    assert repository.current(rule.symbol, rule.scope)["started_at"] == campaign["started_at"]
