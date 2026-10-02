from datetime import timedelta
import json
import sys

import pytest

from intraday.contracts import DecisionScope, PerpRuleParameters, ScopedRuleCandidate
from intraday.store import IntradayStore
from intraday.replay_v2.collection import inherit_perp_collection, read_collection_binding
from intraday.replay_v2.lifecycle import _candidate, _replay_inputs, replay_gate
from test_replay_v2_data import NOW, record_decision, dump


def source(tmp_path):
    store = IntradayStore(tmp_path / "source.sqlite")
    store.register_asset("DOGE", market="perp", now=NOW)
    champion = ScopedRuleCandidate.create(rule_id="champion", parent_rule_id="root", thesis_id="test",
        symbol="DOGEUSDT", scope=DecisionScope.PERP_INTRADAY, parameters=PerpRuleParameters(),
        created_at=NOW, model_ref="operator", prompt_version="test")
    store.register_scoped_rule(champion)
    store.activate_scoped_champion(champion.scope, champion.rule_id, now=NOW, symbol=champion.symbol)
    record_decision(store, at=NOW + timedelta(seconds=1))
    return store, champion


def test_explicit_inheritance_keeps_champion_and_uses_old_collection_without_backdating(tmp_path):
    store, champion = source(tmp_path)
    registry = store.scoped_rule_registry(champion.scope, symbol=champion.symbol)
    later = NOW + timedelta(days=7)
    saved = inherit_perp_collection(store, "DOGE", collection_from=NOW, now=later)
    candidate = store.load_scoped_rule(saved["candidate_id"])
    assert candidate.parameters == champion.parameters
    assert candidate.parent_rule_id == champion.rule_id
    assert candidate.created_at == later
    assert store.load_active_scoped_rule(champion.scope, symbol=champion.symbol) == champion
    assert store.scoped_rule_registry(champion.scope, symbol=champion.symbol) == registry
    assert inherit_perp_collection(store, "DOGE", collection_from=NOW, now=later+timedelta(days=1)) == saved
    reader = IntradayStore(store.database, read_only=True)
    with reader.read_snapshot():
        rule, current = _candidate(reader, candidate.rule_id)
        config, _, evidence = _replay_inputs(reader, rule, current, later, root=tmp_path, funding_id=None)
    assert config.start == NOW
    assert evidence.elapsed_days == 7
    evaluation = replay_gate(store, candidate.rule_id, now=later, report_dir=tmp_path / "reports")
    assert evaluation.status == "deferred"
    assert "minimum_14_days" in evaluation.reason_codes


def test_unverified_history_does_not_create_candidate_or_binding(tmp_path):
    store, _ = source(tmp_path)
    with store._connect() as c:
        c.execute("UPDATE model_calls SET status='error'")
    before = dump(store.database)
    with pytest.raises(ValueError, match="provenance"):
        inherit_perp_collection(store, "DOGE", collection_from=NOW, now=NOW+timedelta(days=7))
    assert dump(store.database) == before


def test_binding_is_immutable_and_changed_source_is_not_reused(tmp_path):
    store, _ = source(tmp_path)
    later = NOW + timedelta(days=7)
    saved = inherit_perp_collection(store, "DOGE", collection_from=NOW, now=later)
    with store._connect() as c:
        with pytest.raises(Exception, match="immutable"):
            c.execute("UPDATE perp_gate_collections SET payload_json='{}'")
    record_decision(store, at=NOW+timedelta(hours=1))
    with pytest.raises(ValueError, match="evidence changed"):
        replay_gate(store, saved["candidate_id"], now=later, report_dir=tmp_path / "reports")


def test_optional_reader_never_installs_extension_and_bad_dates_do_not_write(tmp_path):
    store, _ = source(tmp_path)
    before = dump(store.database)
    assert read_collection_binding(IntradayStore(store.database, read_only=True), "missing") is None
    for start in (NOW.replace(tzinfo=None), NOW+timedelta(days=8)):
        with pytest.raises(ValueError):
            inherit_perp_collection(store, "DOGE", collection_from=start, now=NOW+timedelta(days=7))
    assert dump(store.database) == before


def test_pending_competition_blocks_migration(tmp_path):
    store, champion = source(tmp_path)
    candidate = ScopedRuleCandidate.create(rule_id="other", parent_rule_id=champion.rule_id, thesis_id="test",
        symbol=champion.symbol, scope=champion.scope, parameters=champion.parameters,
        created_at=NOW, model_ref="operator", prompt_version="test")
    store.register_scoped_rule(candidate)
    with pytest.raises(ValueError, match="another candidate"):
        inherit_perp_collection(store, "DOGE", collection_from=NOW, now=NOW+timedelta(days=7))


def test_cli_inheritance_and_readiness_show_collection_without_changing_champion(tmp_path, monkeypatch, capsys):
    from intraday.__main__ import main
    from intraday.asset_readiness import build_asset_readiness
    store, champion = source(tmp_path)
    monkeypatch.setattr(sys, "argv", ["aigt", "assets", "rules", "inherit-perp", "DOGE",
        "--collection-from", NOW.isoformat(), "--database", str(store.database)])
    main()
    binding = json.loads(capsys.readouterr().out)
    before = dump(store.database)
    row = build_asset_readiness(store.database, symbol="DOGE", market="perp", now=NOW+timedelta(days=7))["rows"][0]
    assert row["gate_version"] == "gate-v2.1"
    assert row["phase"] == "decision_soak"
    assert row["champion_id"] == champion.rule_id
    assert row["candidate_id"] == binding["candidate_id"]
    assert row["started_at"] == NOW.isoformat()
    assert row["earliest_evaluation_at"] == (NOW+timedelta(days=14)).isoformat()
    assert row["next_action"] == "wait"
    assert dump(store.database) == before


def test_rule_binding_cannot_be_used_with_changed_parameters_or_other_scope(tmp_path):
    from intraday.replay_v2.collection import verify_collection_binding
    store, _ = source(tmp_path)
    later = NOW+timedelta(days=7)
    saved = inherit_perp_collection(store, "DOGE", collection_from=NOW, now=later)
    reader = IntradayStore(store.database, read_only=True)
    with reader.read_snapshot():
        binding = read_collection_binding(reader, saved["candidate_id"])
        rule = reader.load_scoped_rule(saved["candidate_id"])
        altered = rule.model_copy(update={"parameters": rule.parameters.model_copy(update={"confidence_threshold": .75})})
        for bad in (altered, rule.model_copy(update={"symbol": "ETHUSDT"}),
                    rule.model_copy(update={"scope": DecisionScope.SPOT_4H})):
            with pytest.raises(ValueError, match="binding mismatch"):
                verify_collection_binding(reader, bad, binding, now=later)


def test_old_collection_never_counts_as_new_validation(tmp_path):
    from intraday.replay_v2.lifecycle import start_gate_soak, evaluate_gate_soak
    from test_replay_gate_perp_e2e import phase
    store, champion = source(tmp_path)
    root = tmp_path / "reports"
    end, funding = phase(store, NOW, root)
    binding = inherit_perp_collection(store, "DOGE", collection_from=NOW, now=end)
    rule_id = binding["candidate_id"]
    gate = replay_gate(store, rule_id, now=end, report_dir=root, funding_id=funding)
    assert gate.status == "pass", gate.reason_codes
    start_gate_soak(store, rule_id, evaluation_id=gate.evaluation_id, now=end, report_dir=root)
    early = evaluate_gate_soak(store, rule_id, now=end+timedelta(hours=1), report_dir=root)
    assert early.replay_config.start == end
    assert early.metrics["outcome_count"] == 0
    assert "minimum_14_days_post_gate" in early.reason_codes
    later, new_funding = phase(store, end, root)
    result = evaluate_gate_soak(store, rule_id, now=later, report_dir=root, funding_id=new_funding)
    assert result.status == "pass", result.reason_codes
    assert result.metrics["outcome_count"] == 100
    assert result.replay_config.start == end
    assert store.load_active_scoped_rule(champion.scope, symbol=champion.symbol) == champion


def test_inherited_candidate_cannot_fall_back_to_legacy_lifecycle(tmp_path):
    from intraday.perp_bootstrap_lifecycle import start_perp_decision_soak, replay_perp_bootstrap
    store, _ = source(tmp_path)
    later = NOW+timedelta(days=7)
    saved = inherit_perp_collection(store, "DOGE", collection_from=NOW, now=later)
    before = dump(store.database)
    for operation in (start_perp_decision_soak, replay_perp_bootstrap):
        with pytest.raises(ValueError, match="no v1 fallback"):
            operation(store, saved["candidate_id"], now=later)
    assert dump(store.database) == before


def test_collection_clock_cannot_start_before_archived_decisions(tmp_path):
    store, _ = source(tmp_path)
    before = dump(store.database)
    with pytest.raises(ValueError, match="precedes recorded evidence"):
        inherit_perp_collection(store, "DOGE", collection_from=NOW-timedelta(days=10), now=NOW+timedelta(days=7))
    assert dump(store.database) == before
