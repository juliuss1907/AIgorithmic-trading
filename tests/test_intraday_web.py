import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from intraday.contracts import (
    AnalysisAssessment,
    AnalystReport,
    DecisionScope,
    FeatureSnapshot,
    HorizonThesis,
    MarketThesisBundle,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
)
from intraday.store import IntradayStore
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.web import create_app


def test_dashboard_and_status_are_available_without_control_credentials(tmp_path):
    client = TestClient(create_app(database=tmp_path / "intraday.sqlite"))

    page = client.get("/")
    legacy = client.get("/legacy-intraday")
    status = client.get("/api/status")

    assert page.status_code == 200
    assert "AIGT operations" in page.text
    assert "Waiting for first model-backed signal" in page.text
    assert "Recent Jev signals" in page.text
    assert 'href="/portfolio"' in page.text
    assert legacy.status_code == 200
    assert "Intraday control room" in legacy.text
    assert "Paper only" in legacy.text
    assert "Hyperliquid" in legacy.text
    assert status.json()["mode"] == "paper"
    assert status.json()["counts"]["decisions"] == 0
    assert status.json()["cross_venue"]["venue"] == "hyperliquid"


def test_system_plan_and_architecture_are_public_read_only_pages(tmp_path):
    client = TestClient(create_app(database=tmp_path / "intraday.sqlite"))

    plan = client.get("/system-plan")
    architecture = client.get("/static/system-trading-architecture.html")
    dashboard = client.get("/")
    portfolio = client.get("/portfolio")

    assert plan.status_code == 200
    assert "System plan" in plan.text
    assert "Một bot paper-trading an toàn" in plan.text
    assert "system-trading-architecture.html" in plan.text
    assert 'http-equiv="refresh"' not in plan.text
    assert architecture.status_code == 200
    assert "AIgorithmic Trading" in architecture.text
    assert 'href="/system-plan"' in dashboard.text
    assert 'href="/system-plan"' in portfolio.text


def test_control_endpoint_is_token_protected_and_deduplicated(tmp_path):
    client = TestClient(
        create_app(database=tmp_path / "intraday.sqlite", control_token="correct-token")
    )
    body = {"command_id": "cmd-1", "kind": "pause_entries"}

    assert client.post("/api/commands", json=body).status_code == 401
    accepted = client.post(
        "/api/commands",
        json=body,
        headers={"Authorization": "Bearer correct-token"},
    )
    repeated = client.post(
        "/api/commands",
        json=body,
        headers={"Authorization": "Bearer correct-token"},
    )

    assert accepted.status_code == 202
    assert accepted.json() == repeated.json()
    assert accepted.json()["actor"] == "dashboard"


def test_decision_api_has_a_bounded_limit(tmp_path):
    client = TestClient(create_app(database=tmp_path / "intraday.sqlite"))

    assert client.get("/api/decisions?limit=0").status_code == 422
    assert client.get("/api/decisions?limit=1001").status_code == 422
    assert client.get("/api/decisions?limit=20").json() == []


def test_provider_and_analyst_read_apis_are_redacted(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    profile = ProviderProfile.create(
        profile_id="jev-openrouter",
        role=ProviderRole.JEV,
        kind=ProviderKind.OPENROUTER_DECISIONS,
        base_url="https://openrouter.ai/api/alpha/decisions",
        model="typesafe/jev-1.13",
        credential_version="credential-v1",
        created_at=now,
        updated_at=now,
    )
    store.sync_provider_profile(profile)
    client = TestClient(create_app(database=database))

    providers = client.get("/api/providers")
    analysts = client.get("/api/analysts")

    assert providers.status_code == 200
    assert providers.json()["profiles"][0]["profile_id"] == profile.profile_id
    assert "api_key" not in providers.text
    assert "secret" not in providers.text.lower()
    assert analysts.json() == {"reports": {}, "thesis": None, "daily_cost_usd": 0.0}


def test_analyst_api_and_dashboard_show_latest_scoped_thesis_bundle(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc)
    reports = tuple(
        AnalystReport(
            report_id=f"{analyst}-report",
            analyst=analyst,
            assessment=AnalysisAssessment(
                summary=f"{analyst.title()} evidence supports a cautious market posture.",
                stance="neutral",
                confidence=0.6,
                key_findings=("Evidence remains mixed",),
                risk_factors=("Unexpected headline",),
            ),
            generated_at=now,
            model_ref="llm-main@fingerprint",
            prompt_version="analysis-v2",
            input_hash=(str(index) * 64),
        )
        for index, analyst in enumerate(("market", "news", "sentiment"), start=1)
    )
    bundle = MarketThesisBundle(
        thesis_id="bundle-1",
        intraday=HorizonThesis(
            scope=DecisionScope.PERP_INTRADAY,
            summary="Intraday evidence supports a cautious neutral posture.",
            stance="neutral",
            confidence=0.64,
            key_levels={"support": 98_000, "resistance": 102_000},
            risk_factors=("Funding reversal",),
            horizon_minutes=240,
        ),
        daily_swing=HorizonThesis(
            scope=DecisionScope.SPOT_DAILY,
            summary="Daily evidence supports patience above established support.",
            stance="neutral",
            confidence=0.61,
            key_levels={"support": 95_000, "resistance": 110_000},
            risk_factors=("Macro reversal",),
            horizon_minutes=2_880,
        ),
        source_report_ids=tuple(report.report_id for report in reports),
        generated_at=now,
        model_ref="llm-main@fingerprint",
        prompt_version="analysis-v2",
    )
    store.record_scoped_analysis(reports, bundle)
    client = TestClient(create_app(database=database))

    analysts = client.get("/api/analysts")
    dashboard = client.get("/")

    assert analysts.status_code == 200
    assert analysts.json()["thesis"]["thesis_id"] == "bundle-1"
    assert analysts.json()["thesis"]["intraday"]["horizon_minutes"] == 240
    assert dashboard.status_code == 200
    assert "Intraday evidence supports a cautious neutral posture." in dashboard.text


def test_unified_dashboard_uses_safe_signal_projection(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime.now(timezone.utc)
    store.record_journal_signal(
        decision_id="dashboard-safe-signal",
        timestamp=now,
        symbol="BTCUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        state_snapshot='{"secret_marker":"must-not-render"}',
        raw_signals={"price": 100_000.0},
        jev_answers={
            "direction": {"choice": "Buy", "probabilities": {"Buy": 0.91}},
            "regime": {"choice": "Trending Up"},
            "toxic_flow": {"noul": 0.12},
            "entry_quality": {"score": 3.0},
            "risk_level": {"choice": "Low"},
        },
        gate_passed=False,
        gate_reason="soak_observation_only",
        rules_version="perp-v1",
        llm_thesis='{"private":"must-not-render"}',
        market="binance_usdm_perp",
        feature_schema_version="2",
    )

    page = TestClient(create_app(database=database)).get("/")

    assert page.status_code == 200
    assert "Buy" in page.text
    assert "91%" in page.text
    assert "OBSERVED" in page.text
    assert "must-not-render" not in page.text


def test_provider_mutations_are_authenticated_idempotent_commands(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    profile = ProviderProfile.create(
        profile_id="llm-main",
        role=ProviderRole.LLM,
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://api.example.com/v1",
        model="example/model",
        credential_version="credential-v1",
        created_at=now,
        updated_at=now,
    )
    store.sync_provider_profile(profile)
    client = TestClient(create_app(database=database, control_token="control-token"))
    headers = {
        "Authorization": "Bearer control-token",
        "Idempotency-Key": "provider-test-1",
    }

    assert client.post(f"/api/providers/{profile.profile_id}/tests").status_code == 401
    first = client.post(f"/api/providers/{profile.profile_id}/tests", headers=headers)
    repeated = client.post(f"/api/providers/{profile.profile_id}/tests", headers=headers)

    assert first.status_code == 202
    assert first.json() == repeated.json()
    assert first.json()["kind"] == "provider_test"
    assert json.loads(first.json()["payload_json"]) == {"profile_id": profile.profile_id}

    activate = client.put(
        "/api/provider-assignments/llm",
        json={"profile_id": profile.profile_id},
        headers={**headers, "Idempotency-Key": "provider-activate-1"},
    )
    assert activate.status_code == 202
    assert activate.json()["kind"] == "provider_activate"


def test_combined_portfolio_page_api_and_control_inbox(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    state = ParentPortfolioState(
        mark_price=100_000,
        day_start_equity=10_000,
        high_water_mark=10_000,
        entries_paused=True,
        paper_active=False,
        updated_at=now,
    )
    store.save_parent_portfolio_state(state, event_kind="initialized", actor="test")
    store.record_snapshot(
        FeatureSnapshot.create(
            symbol="BTCUSDT",
            market="binance_usdm_perp",
            timeframe="1h",
            feature_schema_version="2",
            event_time=now,
            built_at=now,
            bid=109_990,
            ask=110_010,
            features={
                "price": 109_000,
                "mark_price": 110_000,
                "reference_price": 110_000,
                "candle_close_price": 109_000,
            },
            freshness={"candles": True, "order_book": True},
        )
    )
    store.record_snapshot(
        FeatureSnapshot.create(
            symbol="BTCUSDT",
            market="binance_spot",
            timeframe="1d",
            feature_schema_version="2",
            event_time=now,
            built_at=now,
            bid=89_990,
            ask=90_010,
            features={
                "price": 89_000,
                "reference_price": 90_000,
                "candle_close_price": 89_000,
            },
            freshness={"candles": True, "order_book": True},
        )
    )
    client = TestClient(create_app(database=database, control_token="control-token"))

    page = client.get("/portfolio")
    api = client.get("/api/portfolio")
    operations = client.get("/api/operations")
    unauthorized = client.post(
        "/api/portfolio/commands",
        json={"kind": "pause"},
        headers={"Idempotency-Key": "pause-1"},
    )
    accepted = client.post(
        "/api/portfolio/commands",
        json={"kind": "flatten"},
        headers={
            "Authorization": "Bearer control-token",
            "Idempotency-Key": "flatten-1",
        },
    )

    assert page.status_code == 200
    assert "Combined paper portfolio" in page.text
    assert "60 / 40" in page.text
    assert "Spot reference" in page.text
    assert "Perp mark" in page.text
    assert "90000.00" in page.text
    assert "110000.00" in page.text
    assert "Cadence health" in page.text
    assert "Decision experiment" in page.text
    assert "Outcome coverage" in page.text
    assert "Latest retrospective" in page.text
    assert api.json()["portfolio"]["paper_active"] is False
    assert api.json()["portfolio"]["spot_price"] == 90_000
    assert api.json()["portfolio"]["perp_mark_price"] == 110_000
    assert api.json()["limits"]["leverage"] == 3
    assert operations.status_code == 200
    assert operations.json()["schema_version"] == 18
    assert operations.json()["scheduler"] == []
    assert set(operations.json()["experiments"]) == {"spot_daily", "perp_intraday"}
    assert operations.json()["daily_model_cost_usd"] == 0.0
    assert unauthorized.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json()["kind"] == "portfolio_flatten"
