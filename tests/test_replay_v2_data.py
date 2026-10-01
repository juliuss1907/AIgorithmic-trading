from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest

from intraday.contracts import (DecisionScope, FeatureSnapshot, ModelCallRecord,
                                PerpRuleParameters, ProviderRole, ScopedRuleCandidate,
                                SpotRuleParameters)
from intraday.providers import JevDecisionProvider
from intraday.replay_v2.contracts import ReplayConfig
from intraday.replay_v2.data import load_dataset
from intraday.store import IntradayStore


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def rule(symbol="DOGEUSDT", market="spot"):
    return ScopedRuleCandidate.create(
        rule_id="research-rule", parent_rule_id="bootstrap", thesis_id="offline",
        symbol=symbol, scope=DecisionScope.SPOT_4H if market == "spot" else DecisionScope.PERP_INTRADAY,
        parameters=SpotRuleParameters() if market == "spot" else PerpRuleParameters(),
        created_at=NOW, model_ref="operator", prompt_version="test")


def config(market="spot"):
    return ReplayConfig(symbol="DOGE", market=market, rule_id="research-rule",
                        start=NOW, end=NOW + timedelta(days=1))


def dump(path):
    with sqlite3.connect(path) as c:
        return list(c.iterdump())


def source(tmp_path, market="spot"):
    store = IntradayStore(tmp_path / "source.sqlite")
    store.register_asset("DOGE", market=market, now=NOW)
    store.register_scoped_rule(rule(market=market))
    return store


def snapshot(at=NOW):
    return FeatureSnapshot.create(symbol="DOGEUSDT", feature_schema_version="2",
        event_time=at, built_at=at, bid=99.999, ask=100.001,
        features={"mark_price": 100., "reference_price": 100.},
        freshness={name: True for name in ("candles", "order_book", "premium", "open_interest", "long_short_ratio")})


def record_decision(store, *, at=NOW, fingerprint="a" * 64, delay=2):
    snap = snapshot(at)
    store.record_snapshot(snap)
    tick = f"DOGEUSDT:perp_intraday:{int(at.timestamp()*1000)}"
    model_ref = "archived-profile@" + fingerprint[:12]
    decision_id = hashlib.sha256(f"{model_ref}:{tick}:{snap.checksum}:numeric_v1:primary:-".encode()).hexdigest()[:24]
    call_id = hashlib.sha256(f"perp_intraday_entry:{tick}:{fingerprint}:success:None".encode()).hexdigest()[:32]
    store.record_model_call(ModelCallRecord(call_id=call_id, workflow="perp_intraday_entry",
        role=ProviderRole.JEV, profile_id="archived-profile", profile_fingerprint=fingerprint,
        model="test-model", status="success", started_at=at, completed_at=at + timedelta(seconds=delay),
        latency_ms=delay*1000, request_hash="b"*64))
    store.record_journal_signal(decision_id=decision_id, timestamp=at, symbol="DOGEUSDT",
        scope=DecisionScope.PERP_INTRADAY, market="binance_usdm_perp", feature_schema_version="2",
        state_snapshot=json.dumps(JevDecisionProvider._state(snap, DecisionScope.PERP_INTRADAY)),
        raw_signals=snap.features, jev_answers={
            "direction": {"choice": "Buy", "probabilities": {"Buy": .95}},
            "regime": {"choice": "Trending Up"}, "toxic_flow": {"noul": .1},
            "entry_quality": {"score": 3}, "risk_level": {"choice": "Low"}},
        gate_passed=False, gate_reason="decision_soak_only", rules_version="old-rule", llm_thesis=None)


def test_loader_is_read_only_and_accepts_dynamic_coin(tmp_path):
    store = source(tmp_path)
    opening = int(NOW.timestamp()*1000)
    store.record_asset_candles("DOGEUSDT", "4h", [[opening, 100, 101, 99, 100, 5, opening+14_400_000-1]])
    before = dump(store.database)
    data = load_dataset(store.database, config())
    assert len(data.candles) == 1
    assert data.rule.symbol == "DOGEUSDT"
    assert data.v1_reference is None
    assert dump(store.database) == before


def test_loader_does_not_create_missing_db_and_rejects_scope_mismatch(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        load_dataset(path, config())
    assert not path.exists()
    store = source(tmp_path)
    with pytest.raises(ValueError, match="scope"):
        load_dataset(store.database, config("perp"))


def test_historical_provenance_uses_archived_call_not_current_assignment(tmp_path):
    store = source(tmp_path, "perp")
    record_decision(store, delay=20)
    before = dump(store.database)
    data = load_dataset(store.database, config("perp"))
    assert len(data.decisions) == 1
    assert data.decisions[0].available_at == NOW + timedelta(seconds=20)
    assert data.decisions[0].decision.model_ref == "archived-profile@aaaaaaaaaaaa"
    assert data.quotes[0].mark == 100
    assert dump(store.database) == before


def test_unverified_signals_are_not_invented_decisions(tmp_path):
    store = source(tmp_path, "perp")
    record_decision(store)
    with sqlite3.connect(store.database) as c:
        c.execute("UPDATE model_calls SET status='error'")
    data = load_dataset(store.database, config("perp"))
    assert not data.decisions
    assert "unverified_recorded_decisions:1" in data.limitations
