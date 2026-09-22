from datetime import datetime, timezone

from intraday.contracts import RuleCandidate, RuleParameters
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def rule(rule_id, parent):
    return RuleCandidate.create(
        rule_id=rule_id,
        parent_rule_id=parent,
        thesis_id="thesis-1",
        parameters=RuleParameters(),
        created_at=NOW,
        model_ref="stub/analysis-v1",
        prompt_version="rules-v1",
    )


def test_champion_promotion_and_rollback_are_atomic(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.register_rule(rule("champion-1", "genesis"), status="champion")
    store.activate_champion("champion-1", now=NOW)
    store.register_rule(rule("candidate-1", "champion-1"), status="replay_passed")
    store.set_challenger("candidate-1", now=NOW)

    promoted = store.promote_challenger(now=NOW)
    rolled_back = store.rollback_champion(now=NOW)

    assert promoted["champion_id"] == "candidate-1"
    assert promoted["rollback_id"] == "champion-1"
    assert rolled_back["champion_id"] == "champion-1"
    assert store.load_active_rule().rule_id == "champion-1"
