from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from intraday.store import IntradayStore


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_read_only_store_does_not_initialize_or_allow_writes(tmp_path):
    path = tmp_path / "source.sqlite"
    store = IntradayStore(path)
    before = path.read_bytes()
    reader = IntradayStore(path, read_only=True)
    assert reader.asset_catalog() == store.asset_catalog()
    with reader._connect() as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM asset_catalog")
    assert path.read_bytes() == before


def test_perp_previews_match_evaluations_without_persisting(tmp_path):
    from intraday.perp_bootstrap_lifecycle import (
        bootstrap_perp_rule, start_perp_decision_soak,
        preview_perp_bootstrap, replay_perp_bootstrap,
        preview_perp_post_replay, evaluate_perp_post_replay,
    )
    store = IntradayStore(tmp_path / "perp.sqlite")
    candidate = bootstrap_perp_rule(store, "ETHUSDT", now=NOW - timedelta(days=1))
    start_perp_decision_soak(store, candidate.rule_id, now=NOW - timedelta(days=1))
    reader = IntradayStore(store.database, read_only=True)
    before = store.counts()
    for preview_fn, evaluate_fn in (
        (preview_perp_bootstrap, replay_perp_bootstrap),
        (preview_perp_post_replay, evaluate_perp_post_replay),
    ):
        preview = preview_fn(reader, candidate.rule_id, now=NOW)
        assert store.counts() == before
        assert "evaluation_id" not in preview.values
        saved = evaluate_fn(store, candidate.rule_id, now=NOW)
        assert saved.model_dump(exclude={"evaluation_id"}) == preview.values
        before = store.counts()


def test_spot_preview_matches_soak_evaluator_without_persisting(tmp_path):
    from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
    from intraday.spot_4h_lifecycle import preview_spot_4h_soak, evaluate_spot_4h_soak
    store = IntradayStore(tmp_path / "spot.sqlite")
    candidate = ScopedRuleCandidate.create(
        rule_id="eth-readiness-spot", parent_rule_id="bootstrap", thesis_id="baseline",
        symbol="ETHUSDT", scope=DecisionScope.SPOT_4H, parameters=SpotRuleParameters(),
        created_at=NOW, model_ref="test", prompt_version="v1",
    )
    store.register_scoped_rule(candidate)
    store.update_scoped_rule_status(candidate.rule_id, expected="queued", status="replay_passed")
    store.set_scoped_challenger(candidate.rule_id, now=NOW)
    before = store.counts()
    preview = preview_spot_4h_soak(IntradayStore(store.database, read_only=True), candidate.rule_id, now=NOW)
    assert store.counts() == before
    saved = evaluate_spot_4h_soak(store, candidate.rule_id, now=NOW)
    assert saved.model_dump(exclude={"evaluation_id"}) == preview.values


def test_read_only_store_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing" / "source.sqlite"
    with pytest.raises((FileNotFoundError, ValueError)):
        IntradayStore(path, read_only=True)
    assert not path.parent.exists()


def test_read_only_store_rejects_old_schema_without_migrating(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO schema_meta VALUES ('schema_version', '22')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="v23"):
        IntradayStore(path, read_only=True)
    assert path.read_bytes() == before
