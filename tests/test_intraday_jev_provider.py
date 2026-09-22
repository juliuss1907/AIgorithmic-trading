import json
from datetime import datetime, timezone

import pytest

from intraday.contracts import (
    Direction,
    FeatureSnapshot,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
    Regime,
    RiskLevel,
)
from intraday.provider_client import HttpResponse
from intraday.provider_profiles import ProviderCredential
from intraday.providers import JevDecisionProvider, ProviderDecisionError
from intraday.store import IntradayStore

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def profile():
    return ProviderProfile.create(
        profile_id="jev-openrouter",
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
