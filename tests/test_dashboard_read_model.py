import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from intraday.contracts import (
    DecisionScope,
    ModelCallRecord,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
)
from intraday.store import IntradayStore
from intraday.web import create_app


def _activate_provider(
    store: IntradayStore,
    *,
    role: ProviderRole,
    now: datetime,
) -> ProviderProfile:
    kind = (
        ProviderKind.OPENROUTER_DECISIONS
        if role == ProviderRole.JEV
        else ProviderKind.OPENAI_COMPATIBLE
    )
    base_url = (
        "https://openrouter.ai/api/alpha/decisions"
        if role == ProviderRole.JEV
        else "https://api.example.com/v1"
    )
    profile = ProviderProfile.create(
        profile_id=f"{role.value}-dashboard-test",
        role=role,
        kind=kind,
        base_url=base_url,
        model=f"example/{role.value}",
        credential_version="credential-v1",
        created_at=now,
        updated_at=now,
    )
    store.sync_provider_profile(profile)
    store.record_provider_test(
        profile.profile_id,
        status="ok",
        tested_at=now,
        latency_ms=42,
    )
    store.activate_provider(role, profile.profile_id, actor="test", now=now)
    return profile


def _record_signal(store: IntradayStore, *, now: datetime) -> int:
    return store.record_journal_signal(
        decision_id="dashboard-decision-1",
        timestamp=now,
        symbol="BTCUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        state_snapshot=json.dumps(
            {"decision_scope": "perp_intraday", "secret_marker": "never-return"},
            sort_keys=True,
            separators=(",", ":"),
        ),
        raw_signals={"price": 100_000.0, "rsi14": 31.2},
        jev_answers={
            "direction": {
                "choice": "Buy",
                "probabilities": {"Buy": 0.91, "Hold": 0.09},
            },
            "regime": {"choice": "Trending Up"},
            "toxic_flow": {"probability": 0.08},
            "entry_quality": {"score": 3.2},
            "risk_level": {"choice": "Low"},
        },
        gate_passed=False,
        gate_reason="soak_observation_only",
        rules_version="perp-champion-v3",
        llm_thesis='{"private":"never-return"}',
        market="binance_usdm_perp",
        feature_schema_version="2",
    )


def _record_model_call(
    store: IntradayStore,
    *,
    profile: ProviderProfile,
    workflow: str,
    now: datetime,
) -> None:
    store.record_model_call(
        ModelCallRecord(
            call_id=f"call-{profile.role.value}",
            workflow=workflow,
            role=profile.role,
            profile_id=profile.profile_id,
            profile_fingerprint=profile.fingerprint,
            model=profile.model,
            status="success",
            started_at=now - timedelta(seconds=1),
            completed_at=now,
            latency_ms=1000,
            input_tokens=100,
            output_tokens=10,
            cost_usd=0.001,
            request_hash="a" * 64,
            response_hash="b" * 64,
        )
    )


def test_dashboard_api_reports_waiting_when_no_model_backed_soak_exists(tmp_path):
    response = TestClient(create_app(database=tmp_path / "intraday.sqlite")).get(
        "/api/dashboard"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"]["code"] == "WAITING"
    assert payload["soak"]["started_at"] is None
    assert payload["soak"]["progress_pct"] == 0
    assert payload["signals"]["total"] == 0


def test_dashboard_api_projects_live_soak_without_persisting_an_evaluation(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    started_at = now - timedelta(hours=1)
    jev = _activate_provider(store, role=ProviderRole.JEV, now=now)
    llm = _activate_provider(store, role=ProviderRole.LLM, now=now)
    _record_model_call(
        store,
        profile=jev,
        workflow="perp_intraday_entry",
        now=now - timedelta(seconds=10),
    )
    _record_model_call(
        store,
        profile=llm,
        workflow="research_manager",
        now=now - timedelta(minutes=5),
    )
    _record_signal(store, now=started_at)
    store.record_portfolio_soak_tick(
        scope=DecisionScope.PERP_INTRADAY,
        status="success",
        created_at=now - timedelta(seconds=10),
    )
    store.record_portfolio_soak_tick(
        scope=DecisionScope.SPOT_DAILY,
        status="skipped_no_setup",
        created_at=now - timedelta(hours=1),
    )

    payload = TestClient(create_app(database=database)).get("/api/dashboard").json()

    assert payload["status"]["code"] == "SOAK_ACTIVE"
    assert payload["soak"]["evidence_version"] == "scope-price-v2"
    assert payload["soak"]["started_at"] == started_at.isoformat()
    assert 1.3 < payload["soak"]["progress_pct"] < 1.5
    assert payload["scopes"]["spot_daily"]["healthy"] is True
    assert payload["scopes"]["spot_daily"]["latest_status"] == "skipped_no_setup"
    assert payload["scopes"]["perp_intraday"]["latest_status"] == "success"
    assert payload["providers"]["jev"]["active"] is True
    assert payload["providers"]["llm"]["active"] is True
    assert store.latest_portfolio_soak_evaluation() is None


def test_signal_api_is_bounded_scoped_and_never_leaks_model_context(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    signal_id = _record_signal(store, now=now)
    client = TestClient(create_app(database=database))

    response = client.get("/api/signals?limit=20&scope=perp_intraday")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": signal_id,
            "timestamp": now.isoformat(),
            "symbol": "BTCUSDT",
            "scope": "perp_intraday",
            "market": "binance_usdm_perp",
            "direction": "Buy",
            "confidence": 0.91,
            "regime": "Trending Up",
            "toxic_flow": 0.08,
            "entry_quality": 4.2,
            "risk_level": "Low",
            "gate_passed": False,
            "outcome": "OBSERVED",
            "gate_reason": "soak_observation_only",
            "rules_version": "perp-champion-v3",
            "decision_mode": "primary",
        }
    ]
    assert client.get("/api/signals?limit=0").status_code == 422
    assert client.get("/api/signals?limit=101").status_code == 422
    body = response.text.lower()
    assert "state_snapshot" not in body
    assert "raw_signals" not in body
    assert "jev_answers" not in body
    assert "never-return" not in body


def test_dashboard_marks_stale_perp_heartbeat_as_degraded(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    for role in ProviderRole:
        _activate_provider(store, role=role, now=now)
    _record_signal(store, now=now - timedelta(hours=2))
    store.record_portfolio_soak_tick(
        scope=DecisionScope.PERP_INTRADAY,
        status="success",
        created_at=now - timedelta(hours=2),
    )

    payload = TestClient(create_app(database=database)).get("/api/dashboard").json()

    assert payload["status"]["code"] == "DEGRADED"
    assert "perp_heartbeat_stale" in payload["status"]["reasons"]
