from datetime import timedelta

import pytest

from intraday.contracts import DecisionScope
from intraday.perp_bootstrap_lifecycle import start_perp_decision_soak
from intraday.replay_v2.selection import migrate_perp, read_selection, select_rule
from test_replay_v2_data import NOW, dump, record_decision, source


def test_migration_preserves_bootstrap_and_clock_and_is_idempotent(tmp_path):
    store = source(tmp_path, 'perp')
    start_perp_decision_soak(store, 'research-rule', now=NOW)
    record_decision(store, at=NOW+timedelta(seconds=1))
    rule = store.load_scoped_rule('research-rule')
    registry = store.scoped_rule_registry(rule.scope, symbol=rule.symbol)
    before = dump(store.database)
    later = NOW+timedelta(days=2)
    preview = migrate_perp(store, ['DOGE'], now=later, dry_run=True)
    assert preview['rows'][0]['status'] == 'ready', preview
    assert dump(store.database) == before
    saved = migrate_perp(store, ['DOGE'], now=later)
    assert saved['rows'][0]['status'] == 'selected'
    binding = read_selection(store, rule.rule_id)
    assert binding.collection_started_at == NOW
    assert binding.rule_hash == rule.content_hash
    assert store.load_scoped_rule(rule.rule_id) == rule
    assert store.scoped_rule_registry(rule.scope, symbol=rule.symbol) == registry
    assert migrate_perp(store, ['DOGE'], now=later+timedelta(days=1))['rows'][0]['selection'] == binding.model_dump(mode='json')
    with pytest.raises(ValueError, match='v2'):
        start_perp_decision_soak(store, rule.rule_id, now=later)
    from intraday.replay_v2.lifecycle import replay_gate
    replay = replay_gate(store, rule.rule_id, now=later, report_dir=tmp_path/'reports')
    assert replay.replay_config.start == NOW
    assert replay.status == 'deferred'
    assert store.scoped_rule_registry(rule.scope, symbol=rule.symbol) == registry


def test_failed_provenance_does_not_select_or_change_rule(tmp_path):
    store = source(tmp_path, 'perp')
    start_perp_decision_soak(store, 'research-rule', now=NOW)
    record_decision(store)
    with store._connect() as c:
        c.execute("UPDATE model_calls SET status='error'")
    before = dump(store.database)
    result = migrate_perp(store, ['DOGE'], now=NOW+timedelta(days=2))
    assert result['rows'][0]['status'] == 'error'
    assert dump(store.database) == before


def test_spot_selection_is_read_only_until_opt_in_and_blocks_legacy(tmp_path):
    store = source(tmp_path)
    before = dump(store.database)
    assert read_selection(store, 'research-rule') is None
    assert dump(store.database) == before
    saved = select_rule(store, 'research-rule', now=NOW)
    assert saved.scope is DecisionScope.SPOT_4H
    assert saved.collection_started_at is None
    assert store.scoped_rule_status('research-rule') == 'queued'
    from intraday.replay_v2.compatibility import require_legacy_campaign
    with pytest.raises(ValueError, match='v2'):
        require_legacy_campaign(store, 'research-rule')
    with store._connect() as c:
        with pytest.raises(Exception, match='immutable'):
            c.execute("UPDATE gate_v2_selections SET payload_json='{}'")


def test_champion_migration_keeps_champion_and_binding_on_retry(tmp_path):
    from test_perp_collection_migration import source as champion_source
    store, champion = champion_source(tmp_path)
    registry = store.scoped_rule_registry(champion.scope, symbol=champion.symbol)
    result = migrate_perp(store, ['DOGE'], now=NOW+timedelta(days=3))
    assert result['rows'][0]['status'] == 'selected', result
    candidate_id = result['rows'][0]['selection']['candidate_id']
    assert store.load_scoped_rule(candidate_id).parameters == champion.parameters
    assert store.scoped_rule_registry(champion.scope, symbol=champion.symbol) == registry
    retry = migrate_perp(store, ['DOGE'], now=NOW+timedelta(days=4))
    assert retry['rows'][0]['selection'] == result['rows'][0]['selection']
