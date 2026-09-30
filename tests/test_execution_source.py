import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import DecisionScope, FeatureSnapshot, PerpRuleParameters, ProviderKind, ProviderProfile, ProviderRole, ScopedRuleCandidate
from intraday.execution.source import EvidenceSource
from intraday.portfolio_soak import PortfolioSoakEvaluation, run_soak_cycle
from intraday.provider_client import HttpResponse
from intraday.provider_profiles import ProviderCredential
from intraday.providers import JevDecisionProvider
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 30, 3, tzinfo=timezone.utc)


def source_fixture(tmp_path):
    path = tmp_path / "source.sqlite3"
    store = IntradayStore(path)
    profile = ProviderProfile.create(profile_id="jev-test", role=ProviderRole.JEV,
        kind=ProviderKind.OPENROUTER_DECISIONS, base_url="https://openrouter.ai/api/alpha/decisions",
        model="typesafe/jev-1.13", credential_version="v1", created_at=NOW, updated_at=NOW)
    store.sync_provider_profile(profile)
    store.record_provider_test(profile.profile_id, status="ok", tested_at=NOW, latency_ms=1)
    store.activate_provider(ProviderRole.JEV, profile.profile_id, actor="test", now=NOW)
    rule = ScopedRuleCandidate.create(rule_id="btc-perp-rule", parent_rule_id="seed", thesis_id="seed",
        scope=DecisionScope.PERP_INTRADAY, parameters=PerpRuleParameters(), created_at=NOW,
        model_ref="operator-seed", prompt_version="seed")
    store.register_scoped_rule(rule)
    store.activate_scoped_champion(rule.scope, rule.rule_id, now=NOW)
    answers = {"direction": {"choice": "Buy", "probabilities": {"Buy": .95}},
               "regime": {"choice": "Trending Up"}, "risk_level": {"choice": "Low"},
               "entry_quality": {"score": 3}, "toxic_flow": {"noul": .1}}
    provider = JevDecisionProvider(ProviderCredential(profile, "fixture-only"), store=store,
        transport=lambda **_: HttpResponse(200, {}, json.dumps({"answers": answers}).encode()))
    snapshot = FeatureSnapshot.create(symbol="BTCUSDT", market="binance_usdm_perp", feature_schema_version="2",
        event_time=NOW, built_at=NOW, bid=99990, ask=100010, features={"mark_price": 100000, "price": 100000},
        freshness={name: True for name in ("candles", "order_book", "premium", "open_interest", "long_short_ratio")})
    run_soak_cycle(store, provider, snapshot, now=NOW, scopes=(DecisionScope.PERP_INTRADAY,))
    store.record_portfolio_soak_evaluation(PortfolioSoakEvaluation(
        evaluation_id="0123456789abcdef", evidence_version="scope-price-v2", status="pass",
        started_at=NOW-timedelta(hours=73), evaluated_at=NOW, duration_hours=73,
        sample_counts={"perp_intraday": 100, "spot_daily": 4}, availability={"perp_intraday": 1, "spot_daily": 1},
        hard_risk_violations=0))
    return path


def test_actual_source_verifies_primary_model_rule_and_preserves_database(tmp_path):
    path = source_fixture(tmp_path)
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    source = EvidenceSource(path)
    assert source.evaluation("0123456789abcdef", now=NOW).status == "pass"
    assert source.rule().rule_id == "btc-perp-rule"
    snapshot, decision, rule_id = source.latest_decision(now=NOW)
    assert decision.model_ref.startswith("jev-test@")
    assert snapshot.market == "binance_usdm_perp"
    assert rule_id == source.rule().rule_id
    with source.connect() as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM signals")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original


@pytest.mark.parametrize("mutation", ["missing_call", "wrong_decision", "snapshot_tamper", "shadow"])
def test_source_rejects_unverified_provenance(tmp_path, mutation):
    path = source_fixture(tmp_path)
    with sqlite3.connect(path) as connection:
        # Corrupt only this test-owned fixture to exercise the read boundary.
        connection.execute("DROP TRIGGER signals_append_only_update")
        if mutation == "missing_call":
            connection.execute("DELETE FROM model_calls")
        elif mutation == "wrong_decision":
            connection.execute("UPDATE signals SET decision_id='stub-decision'")
        elif mutation == "snapshot_tamper":
            saved = json.loads(connection.execute("SELECT payload_json FROM snapshots").fetchone()[0])
            saved["features"]["mark_price"] = 1
            connection.execute("UPDATE snapshots SET payload_json=?", (json.dumps(saved),))
        else:
            connection.execute("UPDATE signals SET decision_mode='shadow'")
    with pytest.raises(ValueError):
        EvidenceSource(path).latest_decision(now=NOW)


def test_source_rejects_stale_signal_and_failed_latest_evaluation(tmp_path):
    path = source_fixture(tmp_path)
    source = EvidenceSource(path)
    with pytest.raises(ValueError, match="stale"):
        source.latest_decision(now=NOW+timedelta(seconds=46))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE portfolio_soak_evaluations SET status='reject'")
    with pytest.raises(ValueError, match="passing"):
        source.evaluation("0123456789abcdef", now=NOW)
