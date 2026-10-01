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


def test_readiness_filters_dynamic_catalog_and_does_not_write(tmp_path):
    from intraday.asset_readiness import build_asset_readiness
    store = IntradayStore(tmp_path / "source.sqlite")
    store.register_asset("DOGE", market="spot", now=NOW)
    before = store.database.read_bytes()
    report = build_asset_readiness(store.database, symbol="doge", now=NOW)
    assert report["report_schema_version"] == "1"
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["scope"] == "spot_4h" and row["symbol"] == "DOGEUSDT"
    assert row["phase"] == "no_rule" and row["venue"] is None
    assert "can_trade" not in row
    assert build_asset_readiness(store.database, symbol="DOGE", market="perp", now=NOW)["rows"] == []
    assert store.database.read_bytes() == before


def test_perp_readiness_has_14_day_lower_bound_and_no_evaluation_id(tmp_path):
    from intraday.asset_readiness import build_asset_readiness
    from intraday.perp_bootstrap_lifecycle import bootstrap_perp_rule, start_perp_decision_soak
    store = IntradayStore(tmp_path / "source.sqlite")
    started = NOW - timedelta(days=2)
    rule = bootstrap_perp_rule(store, "ETHUSDT", now=started)
    start_perp_decision_soak(store, rule.rule_id, now=started)
    before = store.counts()
    row = build_asset_readiness(store.database, symbol="ETH", market="perp", now=NOW)["rows"][0]
    assert row["phase"] == "decision_soak"
    assert row["earliest_evaluation_at"] == (started + timedelta(days=14)).isoformat()
    assert row["preview"]["status"] == "deferred"
    assert "evaluation_id" not in row["preview"]
    assert "minimum_100_matured_15m_outcomes" in row["blockers"]
    assert row["next_action"] == "wait"
    assert store.counts() == before


def test_readiness_legacy_champion_does_not_rebootstrap(tmp_path):
    from intraday.asset_readiness import build_asset_readiness
    from intraday.perp_bootstrap_lifecycle import bootstrap_perp_rule
    from intraday.contracts import DecisionScope
    store = IntradayStore(tmp_path / "source.sqlite")
    rule = bootstrap_perp_rule(store, "BTCUSDT", now=NOW)
    store.activate_scoped_champion(DecisionScope.PERP_INTRADAY, rule.rule_id, now=NOW)
    row = build_asset_readiness(store.database, symbol="BTC", market="perp", now=NOW)["rows"][0]
    assert row["phase"] == "champion"
    assert row["legacy_champion"] is True
    assert row["earliest_evaluation_at"] is None
    assert row["next_action"] == "none"


def test_perp_readiness_uses_only_post_replay_samples_and_72h_boundary(tmp_path):
    from intraday.asset_readiness import build_asset_readiness
    from intraday.perp_bootstrap_lifecycle import bootstrap_perp_rule, start_perp_decision_soak, replay_perp_bootstrap
    from tests.test_perp_bootstrap_lifecycle import _add_outcomes, _add_heartbeats
    store = IntradayStore(tmp_path / "source.sqlite")
    started = NOW - timedelta(days=17)
    rule = bootstrap_perp_rule(store, "ETHUSDT", now=started)
    start_perp_decision_soak(store, rule.rule_id, now=started)
    cutoff = NOW - timedelta(hours=72)
    _add_outcomes(store, "pre", started + timedelta(hours=1), 100, timedelta(hours=3, minutes=20))
    _add_heartbeats(store, started, 14 * 24 * 120)
    pre = build_asset_readiness(store.database, symbol="ETH", market="perp", now=cutoff)["rows"][0]
    assert pre["preview"]["status"] == "pass" and pre["next_action"] == "run_replay"
    assert pre["replay"] is None
    replay = replay_perp_bootstrap(store, rule.rule_id, now=cutoff)
    assert replay.status == "pass"
    early = build_asset_readiness(store.database, symbol="ETH", market="perp", now=cutoff + timedelta(hours=1))["rows"][0]
    assert early["phase"] == "post_replay_validation"
    assert early["preview"]["sample_count"] == 0
    assert datetime.fromisoformat(early["replay_cutoff"]) == cutoff
    assert early["earliest_evaluation_at"] == NOW.isoformat()
    _add_outcomes(store, "post", cutoff + timedelta(hours=1), 100, timedelta(minutes=30))
    _add_heartbeats(store, cutoff, 72 * 120)
    ready = build_asset_readiness(store.database, symbol="ETH", market="perp", now=NOW)["rows"][0]
    assert ready["preview"]["status"] == "pass" and ready["next_action"] == "run_evaluation"
    assert ready["soak"] is None
    assert ready["preview"]["metrics"]["outcomes"] == 100
    almost = build_asset_readiness(store.database, symbol="ETH", market="perp", now=NOW - timedelta(seconds=1))["rows"][0]
    assert almost["preview"]["status"] == "deferred"
    assert "minimum_72_hours_post_replay" in almost["blockers"]


def test_spot_replay_is_not_run_on_readiness_and_reject_is_preserved(tmp_path, monkeypatch):
    from intraday.asset_readiness import build_asset_readiness
    from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
    from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation
    import intraday.spot_4h_lifecycle as spot
    store = IntradayStore(tmp_path / "source.sqlite")
    rule = ScopedRuleCandidate.create(
        rule_id="eth-spot-rejected", parent_rule_id="bootstrap", thesis_id="test",
        symbol="ETHUSDT", scope=DecisionScope.SPOT_4H, parameters=SpotRuleParameters(),
        created_at=NOW, model_ref="test", prompt_version="v1",
    )
    store.register_scoped_rule(rule)
    saved = ScopedRuleEvaluation.create(
        candidate_id=rule.rule_id, symbol=rule.symbol, scope=rule.scope,
        kind="replay", status="reject", evaluated_at=NOW, started_at=None,
        sample_count=9, coverage=1., champion_score=0, challenger_score=-6,
        reason_codes=("max_drawdown_at_least_8pct",),
        metrics={"max_drawdown_pct": 11.03},
    )
    store.record_scoped_rule_evaluation(saved)
    store.update_scoped_rule_status(rule.rule_id, expected="queued", status="rejected")
    monkeypatch.setattr(spot, "_walk_forward", lambda *a, **k: pytest.fail("readiness ran backtest"))
    row = build_asset_readiness(store.database, symbol="ETH", market="spot", now=NOW)["rows"][0]
    assert row["phase"] == "rejected"
    assert row["replay"]["evaluation_id"] == saved.evaluation_id
    assert row["replay"]["metrics"]["max_drawdown_pct"] == 11.03


def test_readiness_disabled_and_unknown_asset(tmp_path):
    from intraday.asset_readiness import build_asset_readiness
    from intraday.contracts import DecisionScope
    from dataclasses import replace
    from intraday.assets import AssetStage
    store = IntradayStore(tmp_path / "source.sqlite")
    lifecycle = store.asset_lifecycle("ETHUSDT", DecisionScope.PERP_INTRADAY)
    store.save_asset_lifecycle(replace(lifecycle, stage=AssetStage.DISABLED), updated_at=NOW)
    row = build_asset_readiness(store.database, symbol="ETH", market="perp", now=NOW)["rows"][0]
    assert row["phase"] == "disabled"
    assert row["next_action"] == "none"
    with pytest.raises(ValueError, match="not registered"):
        build_asset_readiness(store.database, symbol="DOGE", now=NOW)
