import json
import sys
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from intraday.contracts import (
    DecisionMode,
    DecisionScope,
    ModelCallRecord,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
    StateVariant,
    ExternalObservation,
    ScopedRuleCandidate,
    SpotRuleParameters,
)
from intraday.store import IntradayStore
from intraday.__main__ import main
from intraday.dashboard_read_model import build_dashboard_snapshot
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


def _record_signal(
    store: IntradayStore, *, now: datetime, symbol: str = "BTCUSDT",
    decision_id: str = "dashboard-decision-1",
) -> int:
    return store.record_journal_signal(
        decision_id=decision_id,
        timestamp=now,
        symbol=symbol,
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
    suffix: str = "",
) -> None:
    store.record_model_call(
        ModelCallRecord(
            call_id=f"call-{profile.role.value}{suffix}",
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
    assert [item["symbol"] for item in payload["assets"]] == [
        "BTCUSDT", "ETHUSDT", "HYPEUSDT", "NEARUSDT", "ZECUSDT", "SOLUSDT"
    ]
    assert payload["assets"][1]["stages"] == {
        "perp_intraday": "shadow", "spot_daily": "shadow", "spot_4h": "shadow"
    }
    assert payload["assets"][1]["rules"]["spot_daily"]["champion_id"] is None


def test_dashboard_reports_asset_owned_champion(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    rule = ScopedRuleCandidate.create(
        rule_id="eth-spot-champion", parent_rule_id="bootstrap",
        thesis_id="baseline", symbol="ETHUSDT", scope=DecisionScope.SPOT_DAILY,
        parameters=SpotRuleParameters(), created_at=now,
        model_ref="deterministic/baseline", prompt_version="spot-baseline-v1",
    )
    store.register_scoped_rule(rule, status="champion")
    store.activate_scoped_champion(rule.scope, rule.rule_id, now=now, symbol=rule.symbol)

    response = TestClient(create_app(database=database)).get("/api/dashboard")

    eth = next(item for item in response.json()["assets"] if item["symbol"] == "ETHUSDT")
    btc = next(item for item in response.json()["assets"] if item["symbol"] == "BTCUSDT")
    assert eth["rules"]["spot_daily"]["champion_id"] == rule.rule_id
    assert btc["rules"]["spot_daily"]["champion_id"] is None
    page = TestClient(create_app(database=database)).get("/")
    assert page.status_code == 200
    assert "Rules / evidence" in page.text
    assert "eth-spot-champion" in page.text


def test_dashboard_exposes_shadow_source_health_and_latest_metrics(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    store.record_external_observation(ExternalObservation.create(
        source="aster", dataset="perp_market", symbol="BTCUSDT",
        source_timestamp=now, received_at=now,
        metrics={"basis_bps": 4.2, "funding_rate": 0.0001}, labels={},
    ))
    store.record_external_observation(ExternalObservation.create(
        source="aster", dataset="perp_market", symbol="ETHUSDT",
        source_timestamp=now, received_at=now,
        metrics={"basis_bps": 2.1, "funding_rate": 0.0002}, labels={},
    ))

    client = TestClient(create_app(database=database))
    payload = client.get("/api/dashboard").json()

    aster = payload["external_sources"]["aster"]
    assert aster["status"] == "healthy"
    assert aster["total_observations"] == 2
    assert payload["external_sources"]["variational"]["status"] == "missing"
    eth = next(item for item in payload["assets"] if item["symbol"] == "ETHUSDT")
    assert eth["sources"]["aster"]["status"] == "healthy"
    assert eth["sources"]["aster"]["latest"]["metrics"]["basis_bps"] == 2.1
    page = client.get("/")
    assert "Shadow data sources" in page.text
    assert "Asset rollout" in page.text
    assert "ETHUSDT" in page.text
    assert "Aster" in page.text


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


def test_parent_soak_evaluation_and_preview_use_only_anchored_btc_evidence(
    tmp_path, monkeypatch, capsys,
):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
    eth_anchor = now - timedelta(hours=80)
    btc_anchor = now - timedelta(hours=73)
    _record_signal(
        store, now=eth_anchor, symbol="ETHUSDT", decision_id="eth-anchor",
    )
    _record_signal(store, now=btc_anchor)
    store.record_portfolio_soak_tick(
        symbol="ETHUSDT", scope=DecisionScope.SPOT_DAILY,
        status="success", created_at=eth_anchor,
    )
    store.record_portfolio_soak_tick(
        symbol="ETHUSDT", scope=DecisionScope.PERP_INTRADAY,
        status="success", created_at=now - timedelta(minutes=5),
    )
    store.record_portfolio_soak_tick(
        symbol="BTCUSDT", scope=DecisionScope.SPOT_DAILY,
        status="provider_error", hard_risk_violation=True,
        created_at=btc_anchor - timedelta(minutes=7),
    )
    for scope, status in (
        (DecisionScope.SPOT_DAILY, "skipped_no_setup"),
        (DecisionScope.PERP_INTRADAY, "success"),
    ):
        store.record_portfolio_soak_tick(
            symbol="BTCUSDT", scope=scope, status=status,
            created_at=btc_anchor,
        )
    monkeypatch.setattr(sys, "argv", [
        "aigt", "portfolio", "soak", "evaluate", "--database", str(database),
        "--at", now.isoformat(),
    ])

    main()

    persisted = json.loads(capsys.readouterr().out)
    snapshot = build_dashboard_snapshot(store, now=now)
    preview = snapshot["soak"]["preview"]
    assert snapshot["soak"]["started_at"] == btc_anchor.isoformat()
    assert persisted["started_at"] == preview["started_at"]
    assert persisted["sample_counts"] == preview["sample_counts"] == {
        "spot_daily": 1, "perp_intraday": 1,
    }
    assert persisted["hard_risk_violations"] == preview["hard_risk_violations"] == 0


def test_parent_soak_without_btc_anchor_does_not_use_other_asset_ticks(
    tmp_path, monkeypatch, capsys,
):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
    _record_signal(
        store, now=now - timedelta(hours=73), symbol="ETHUSDT",
        decision_id="eth-only-anchor",
    )
    store.record_portfolio_soak_tick(
        symbol="ETHUSDT", scope=DecisionScope.PERP_INTRADAY,
        status="success", created_at=now - timedelta(hours=73),
    )
    monkeypatch.setattr(sys, "argv", [
        "aigt", "portfolio", "soak", "evaluate", "--database", str(database),
        "--at", now.isoformat(),
    ])

    main()

    persisted = json.loads(capsys.readouterr().out)
    assert persisted["started_at"] is None
    assert persisted["status"] == "deferred"
    assert persisted["sample_counts"] == {"spot_daily": 0, "perp_intraday": 0}


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


def test_provider_health_uses_latest_call_per_role_not_only_recent_call_window(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    jev = _activate_provider(store, role=ProviderRole.JEV, now=now)
    llm = _activate_provider(store, role=ProviderRole.LLM, now=now)
    _record_model_call(
        store,
        profile=llm,
        workflow="research_manager",
        now=now - timedelta(minutes=30),
    )
    for index in range(25):
        _record_model_call(
            store,
            profile=jev,
            workflow="perp_intraday_entry",
            now=now - timedelta(seconds=index),
            suffix=f"-{index}",
        )
    _record_signal(store, now=now - timedelta(hours=1))
    store.record_portfolio_soak_tick(
        scope=DecisionScope.PERP_INTRADAY,
        status="success",
        created_at=now,
    )

    payload = TestClient(create_app(database=database)).get("/api/dashboard").json()

    assert payload["providers"]["llm"]["latest_call"]["status"] == "success"
    assert "llm_call_missing" not in payload["status"]["reasons"]


def test_compact_shadow_signal_does_not_start_the_numeric_primary_soak(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    store.record_journal_signal(
        decision_id="compact-shadow-only",
        timestamp=now,
        symbol="BTCUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        state_snapshot='{"variant":"compact"}',
        raw_signals={"price": 100_000.0},
        jev_answers={"direction": {"choice": "Hold"}},
        gate_passed=False,
        gate_reason="shadow_observation_only",
        rules_version="perp-v1",
        llm_thesis=None,
        feature_schema_version="2",
        state_variant=StateVariant.COMPACT_V1,
        decision_mode=DecisionMode.PRIMARY,
    )

    payload = TestClient(create_app(database=database)).get("/api/dashboard").json()

    assert payload["status"]["code"] == "WAITING"
    assert payload["soak"]["started_at"] is None
