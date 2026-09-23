import json
from datetime import datetime, timezone

import pytest

from intraday.contracts import (
    DecisionScope,
    Direction,
    FeatureSnapshot,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
    Regime,
    RiskLevel,
    DecisionMode,
    StateVariant,
)
from intraday.provider_client import HttpResponse
from intraday.provider_profiles import ProviderCredential, ProviderSecretStore
from intraday.engine import IntradayEngine
from intraday.providers import (
    AssignedDecisionProvider,
    JevDecisionProvider,
    ProviderDecisionError,
    StubDecisionProvider,
)
from intraday.store import IntradayStore

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def profile(profile_id="jev-openrouter"):
    return ProviderProfile.create(
        profile_id=profile_id,
        role=ProviderRole.JEV,
        kind=ProviderKind.OPENROUTER_DECISIONS,
        base_url="https://openrouter.ai/api/alpha/decisions",
        model="typesafe/jev-1.13",
        credential_version="credential-v1",
        created_at=NOW,
        updated_at=NOW,
    )


def snapshot():
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=NOW,
        built_at=NOW,
        bid=99_990,
        ask=100_010,
        features={
            "price": 100_000,
            "mark_price": 100_000,
            "rsi14": 55,
            "macd": 12.5,
            "funding_rate": 0.0001,
            "open_interest": 1_000_000,
            "book_imbalance": 0.2,
        },
        freshness={"book": True, "candles": True, "funding": True},
    )


def successful_response():
    return {
        "id": "decision-request-1",
        "model": "typesafe/jev-1.13",
        "answers": {
            "direction": {
                "choice": "Buy",
                "probabilities": {
                    "Strong Buy": 0.02,
                    "Buy": 0.91,
                    "Hold": 0.05,
                    "Take Profit": 0.01,
                    "Sell": 0.01,
                    "Strong Sell": 0.0,
                },
            },
            "regime": {
                "choice": "Trending Up",
                "probabilities": {
                    "Trending Up": 0.9,
                    "Trending Down": 0.02,
                    "Sideways": 0.05,
                    "Volatile": 0.03,
                },
            },
            "toxic_flow": {"noul": 0.12},
            "entry_quality": {"score": 3.2},
            "risk_level": {
                "choice": "Low",
                "probabilities": {"Low": 0.8, "Medium": 0.2, "High": 0, "Critical": 0},
            },
        },
        "usage": {"cost": 0.000021, "prompt_tokens": 500, "completion_tokens": 0},
    }


def test_jev_provider_maps_all_typed_answers_and_records_telemetry(tmp_path):
    calls = []

    def transport(**request):
        calls.append(request)
        return HttpResponse(
            200,
            {"x-request-id": "header-request-1"},
            json.dumps(successful_response()).encode(),
        )

    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile()
    store.sync_provider_profile(current)
    provider = JevDecisionProvider(
        ProviderCredential(current, "private-key"),
        store=store,
        transport=transport,
        clock=lambda: 10.0,
    )

    decision = provider.decide(snapshot(), "BTCUSDT:1", NOW)

    assert decision.direction == Direction.BUY
    assert decision.direction_confidence == 0.91
    assert decision.regime == Regime.TRENDING_UP
    assert decision.toxic_flow == 0.12
    assert decision.entry_quality == 4.2
    assert decision.risk_level == RiskLevel.LOW
    assert decision.provider_request_id == "header-request-1"
    assert decision.model_ref.startswith("jev-openrouter@")
    request = calls[0]
    assert request["timeout"] == 2.5
    payload = json.loads(request["body"])
    assert set(payload["questions"]) == {
        "direction", "regime", "toxic_flow", "entry_quality", "risk_level"
    }
    assert payload["state"]["snapshot_id"] == snapshot().snapshot_id
    telemetry = store.list_model_calls()
    assert telemetry[0].status == "success"
    assert telemetry[0].cost_usd == 0.000021
    assert telemetry[0].input_tokens == 500
    assert "private-key" not in telemetry[0].model_dump_json()


def test_jev_provider_opens_circuit_after_three_failures(tmp_path):
    calls = []

    def transport(**request):
        calls.append(request)
        return HttpResponse(503, {}, b'{"error":"unavailable"}')

    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile()
    store.sync_provider_profile(current)
    provider = JevDecisionProvider(
        ProviderCredential(current, "private-key"),
        store=store,
        transport=transport,
        clock=lambda: 100.0,
    )

    for index in range(3):
        with pytest.raises(ProviderDecisionError, match="provider_unavailable"):
            provider.decide(snapshot(), f"BTCUSDT:{index}", NOW)
    with pytest.raises(ProviderDecisionError, match="circuit_open"):
        provider.decide(snapshot(), "BTCUSDT:4", NOW)

    assert len(calls) == 3
    assert [call.error_code for call in store.list_model_calls()] == [
        "circuit_open", "provider_unavailable", "provider_unavailable", "provider_unavailable"
    ]


def test_scoped_jev_workflows_have_independent_circuit_breakers(tmp_path):
    calls = []
    request_states = []

    def transport(**request):
        payload = json.loads(request["body"])
        calls.append(payload["state"]["decision_scope"])
        request_states.append(payload["state"])
        if payload["state"]["decision_scope"] == "spot_daily":
            return HttpResponse(503, {}, b'{"error":"unavailable"}')
        return HttpResponse(200, {}, json.dumps(successful_response()).encode())

    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile()
    store.sync_provider_profile(current)
    provider = JevDecisionProvider(
        ProviderCredential(current, "private-key"),
        store=store,
        transport=transport,
        clock=lambda: 100.0,
    )

    for index in range(3):
        with pytest.raises(ProviderDecisionError, match="provider_unavailable"):
            provider.decide_scoped(
                snapshot(), f"BTCUSDT:spot:{index}", DecisionScope.SPOT_DAILY, NOW
            )
    with pytest.raises(ProviderDecisionError, match="circuit_open"):
        provider.decide_scoped(
            snapshot(), "BTCUSDT:spot:4", DecisionScope.SPOT_DAILY, NOW
        )

    perp = provider.decide_scoped(
        snapshot(), "BTCUSDT:perp:1", DecisionScope.PERP_INTRADAY, NOW
    )

    assert perp.scope == DecisionScope.PERP_INTRADAY
    assert perp.workflow == "perp_intraday_entry"
    assert perp.decision.direction == Direction.BUY
    sent_state = request_states[-1]
    assert json.loads(perp.trace.state_snapshot) == sent_state
    assert perp.trace.state_snapshot == json.dumps(
        sent_state, sort_keys=True, separators=(",", ":")
    )
    assert perp.trace.raw_signals == {
        "ask": 100_010.0,
        "bid": 99_990.0,
        **snapshot().features,
    }
    assert perp.trace.jev_answers == successful_response()["answers"]
    assert calls == ["spot_daily", "spot_daily", "spot_daily", "perp_intraday"]
    assert {call.workflow for call in store.list_model_calls()} == {
        "spot_daily_entry",
        "perp_intraday_entry",
    }


def test_compact_state_is_categorical_bounded_and_auditable(tmp_path):
    calls = []

    def transport(**request):
        calls.append(json.loads(request["body"]))
        return HttpResponse(200, {}, json.dumps(successful_response()).encode())

    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile()
    provider = JevDecisionProvider(
        ProviderCredential(current, "private-key"),
        store=store,
        transport=transport,
        clock=lambda: 10.0,
    )

    scoped = provider.decide_scoped(
        snapshot(),
        "BTCUSDT:perp:compact",
        DecisionScope.PERP_INTRADAY,
        NOW,
        state_variant=StateVariant.COMPACT_V1,
        decision_mode=DecisionMode.SHADOW,
        experiment_pair_id="pair-1234567890123456",
    )

    state = calls[0]["state"]
    assert list(state) == ["tokens"]
    assert len(state["tokens"]) == 12
    assert all(isinstance(token, str) and ":" in token for token in state["tokens"])
    assert "100000" not in json.dumps(state)
    assert scoped.state_variant == StateVariant.COMPACT_V1
    assert scoped.decision_mode == DecisionMode.SHADOW
    assert scoped.experiment_pair_id == "pair-1234567890123456"
    assert store.list_model_calls()[0].workflow == "perp_intraday_entry_compact"


def test_jev_provider_rejects_invalid_answer_without_returning_partial_decision(tmp_path):
    payload = successful_response()
    del payload["answers"]["direction"]["probabilities"]["Buy"]

    def transport(**request):
        return HttpResponse(200, {}, json.dumps(payload).encode())

    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile()
    store.sync_provider_profile(current)
    provider = JevDecisionProvider(
        ProviderCredential(current, "private-key"),
        store=store,
        transport=transport,
    )

    with pytest.raises(ProviderDecisionError, match="invalid_response"):
        provider.decide(snapshot(), "BTCUSDT:invalid", NOW)

    assert store.list_model_calls()[0].status == "error"
    assert store.list_model_calls()[0].error_code == "invalid_response"


def test_assigned_provider_switches_atomically_on_the_next_tick(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    secrets = ProviderSecretStore(tmp_path / "providers.toml")
    first = profile("jev-first")
    second = profile("jev-second")
    for current in (first, second):
        store.sync_provider_profile(current)
        store.record_provider_test(
            current.profile_id, status="ok", tested_at=NOW, latency_ms=10
        )
        secrets.upsert(current, f"key-{current.profile_id}")
    store.activate_provider(ProviderRole.JEV, first.profile_id, actor="test", now=NOW)
    created = []

    def factory(credential):
        created.append(credential.profile.profile_id)
        direction = Direction.BUY if credential.profile.profile_id == "jev-first" else Direction.SELL
        return StubDecisionProvider(direction=direction)

    assigned = AssignedDecisionProvider(
        store,
        secrets,
        fallback=StubDecisionProvider(direction=Direction.HOLD),
        provider_factory=factory,
    )

    first_decision = assigned.decide(snapshot(), "BTCUSDT:one", NOW)
    store.activate_provider(ProviderRole.JEV, second.profile_id, actor="test", now=NOW)
    second_decision = assigned.decide(snapshot(), "BTCUSDT:two", NOW)

    assert first_decision.direction == Direction.BUY
    assert second_decision.direction == Direction.SELL
    assert created == ["jev-first", "jev-second"]


def test_active_profile_with_missing_secret_fails_closed_in_engine(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = profile()
    store.sync_provider_profile(current)
    store.record_provider_test(
        current.profile_id, status="ok", tested_at=NOW, latency_ms=10
    )
    store.activate_provider(ProviderRole.JEV, current.profile_id, actor="test", now=NOW)
    assigned = AssignedDecisionProvider(
        store,
        ProviderSecretStore(tmp_path / "missing-secrets.toml"),
        fallback=StubDecisionProvider(direction=Direction.BUY),
    )

    result = IntradayEngine(store, assigned, initial_equity=10_000).run_tick(
        snapshot(), now=NOW
    )

    assert result.decision.direction == Direction.HOLD
    assert result.decision.model_ref == "fallback/hold-v1"
    assert result.gate.reason_codes == ("provider_failure",)
