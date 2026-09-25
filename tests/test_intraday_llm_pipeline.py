import json
from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import (
    AnalysisAssessment,
    AnalystReport,
    DecisionScope,
    FeatureSnapshot,
    HorizonThesis,
    MarketThesis,
    MarketThesisBundleAssessment,
    ModelCallRecord,
    PerpRuleParameters,
    PerpRuleProposal,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
    RuleParameters,
    RuleProposal,
    SpotRuleParameters,
    SpotRuleProposal,
    ThesisAssessment,
)
from intraday.llm_pipeline import (
    LLMAnalysisPipeline,
    StructuredLLMError,
    StructuredLLMClient,
    should_generate_rule,
)
from intraday.provider_client import HttpResponse
from intraday.provider_profiles import ProviderCredential, ProviderSecretStore
from intraday.store import IntradayStore
from intraday.runtime import run_analysis_cycle


NOW = datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc)


def llm_profile():
    return ProviderProfile.create(
        profile_id="llm-main",
        role=ProviderRole.LLM,
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://api.example.com/v1",
        model="example/structured-model",
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
            "long_short_ratio": 1.1,
            "book_imbalance": 0.2,
        },
        freshness={"book": True, "candles": True, "funding": True},
    )


def assessment(stance="bullish"):
    return AnalysisAssessment(
        summary="Momentum is constructive but confirmation remains necessary.",
        stance=stance,
        confidence=0.72,
        key_findings=("Momentum is positive", "Funding remains contained"),
        risk_factors=("Macro headline risk",),
    )


def test_structured_llm_client_uses_strict_json_schema_and_audits_call(tmp_path):
    requests = []

    def transport(**request):
        requests.append(request)
        response = {
            "id": "chat-request-1",
            "model": "example/structured-model-202609",
            "choices": [{"message": {"content": assessment().model_dump_json()}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "cost": 0.002},
        }
        return HttpResponse(200, {}, json.dumps(response).encode())

    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = llm_profile()
    store.sync_provider_profile(current)
    client = StructuredLLMClient(
        ProviderCredential(current, "private-llm-key"),
        store=store,
        transport=transport,
    )

    result = client.complete(
        workflow="market_analyst",
        response_model=AnalysisAssessment,
        system_prompt="Analyze only the supplied market facts.",
        input_payload={"price": 100_000},
        now=NOW,
    )

    assert result.stance == "bullish"
    request = requests[0]
    assert request["url"] == "https://api.example.com/v1/chat/completions"
    assert request["timeout"] == 30
    body = json.loads(request["body"])
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    response_schema = body["response_format"]["json_schema"]["schema"]
    assert set(response_schema["required"]) == set(response_schema["properties"])
    assert response_schema["additionalProperties"] is False
    assert response_schema["properties"]["key_findings"]["items"] == {
        "maxLength": 300,
        "minLength": 1,
        "type": "string",
    }
    assert response_schema["properties"]["risk_factors"]["items"] == {
        "maxLength": 300,
        "minLength": 1,
        "type": "string",
    }
    assert body["max_tokens"] == 4096
    assert "provider" not in body
    assert body["tools"] == []
    call = store.list_model_calls()[0]
    assert call.workflow == "market_analyst"
    assert call.status == "success"
    assert call.cost_usd == 0.002
    assert "private-llm-key" not in call.model_dump_json()


def test_structured_llm_client_requires_openrouter_structured_output_support(
    tmp_path,
):
    requests = []

    def transport(**request):
        requests.append(request)
        response = {
            "id": "openrouter-generation-1",
            "model": "openai/gpt-6-luna",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": assessment().model_dump_json(),
                        "refusal": None,
                    },
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40},
        }
        return HttpResponse(200, {}, json.dumps(response).encode())

    current = ProviderProfile.create(
        profile_id="llm-openrouter",
        role=ProviderRole.LLM,
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-6-luna",
        credential_version="credential-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.sync_provider_profile(current)
    client = StructuredLLMClient(
        ProviderCredential(current, "private-llm-key"),
        store=store,
        transport=transport,
    )

    client.complete(
        workflow="market_analyst",
        response_model=AnalysisAssessment,
        system_prompt="Analyze only the supplied market facts.",
        input_payload={"price": 100_000},
        now=NOW,
    )

    body = json.loads(requests[0]["body"])
    assert body["provider"] == {"require_parameters": True}
    assert body["max_tokens"] == 4096
    assert "temperature" not in body


def test_structured_llm_client_classifies_invalid_json_and_keeps_safe_metadata(
    tmp_path,
):
    response = {
        "id": "generation-invalid-json",
        "model": "example/structured-model-202609",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": "{not-json", "refusal": None},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 5, "cost": 0.001},
    }
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = llm_profile()
    store.sync_provider_profile(current)
    client = StructuredLLMClient(
        ProviderCredential(current, "private-llm-key"),
        store=store,
        transport=lambda **request: HttpResponse(
            200, {}, json.dumps(response).encode()
        ),
    )

    with pytest.raises(StructuredLLMError) as captured:
        client.complete(
            workflow="news_analyst",
            response_model=AnalysisAssessment,
            system_prompt="Return structured news analysis.",
            input_payload={"events": []},
            now=NOW,
        )

    assert captured.value.code == "invalid_json"
    call = store.list_model_calls()[0]
    assert call.error_code == "invalid_json"
    assert call.attempt == 1
    assert call.finish_reason == "stop"
    assert call.input_tokens == 100
    assert call.output_tokens == 5
    assert call.cost_usd == 0.001
    assert call.provider_request_id == "generation-invalid-json"
    assert call.response_hash is not None
    assert "not-json" not in call.model_dump_json()
    assert "private-llm-key" not in call.model_dump_json()


def test_structured_llm_client_classifies_truncated_response(tmp_path):
    response = {
        "id": "generation-truncated",
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": '{"summary":"partial"}', "refusal": None},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 4096},
    }
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = llm_profile()
    store.sync_provider_profile(current)
    client = StructuredLLMClient(
        ProviderCredential(current, "private-llm-key"),
        store=store,
        transport=lambda **request: HttpResponse(
            200, {}, json.dumps(response).encode()
        ),
    )

    with pytest.raises(StructuredLLMError) as captured:
        client.complete(
            workflow="news_analyst",
            response_model=AnalysisAssessment,
            system_prompt="Return structured news analysis.",
            input_payload={"events": []},
            now=NOW,
        )

    assert captured.value.code == "truncated_response"
    call = store.list_model_calls()[0]
    assert call.finish_reason == "length"
    assert call.error_code == "truncated_response"


def test_structured_llm_client_classifies_model_refusal(tmp_path):
    response = {
        "id": "generation-refusal",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": None, "refusal": "Unable to comply."},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = llm_profile()
    store.sync_provider_profile(current)
    client = StructuredLLMClient(
        ProviderCredential(current, "private-llm-key"),
        store=store,
        transport=lambda **request: HttpResponse(
            200, {}, json.dumps(response).encode()
        ),
    )

    with pytest.raises(StructuredLLMError) as captured:
        client.complete(
            workflow="news_analyst",
            response_model=AnalysisAssessment,
            system_prompt="Return structured news analysis.",
            input_payload={"events": []},
            now=NOW,
        )

    assert captured.value.code == "model_refusal"
    assert store.list_model_calls()[0].error_code == "model_refusal"


@pytest.mark.parametrize(
    ("kind", "base_url"),
    [
        (ProviderKind.ANTHROPIC_MESSAGES, "https://api.anthropic.com/v1/messages"),
        (ProviderKind.ANTHROPIC_COMPATIBLE, "https://models.example.com/v1/messages"),
    ],
)
def test_structured_llm_client_supports_anthropic_messages(
    kind, base_url, tmp_path
):
    requests = []

    def transport(**request):
        requests.append(request)
        response = {
            "id": "msg_01",
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": assessment().model_dump_json()}],
            "usage": {"input_tokens": 100, "output_tokens": 30},
        }
        return HttpResponse(200, {"request-id": "msg-request-1"}, json.dumps(response).encode())

    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = ProviderProfile.create(
        profile_id="llm-anthropic",
        role=ProviderRole.LLM,
        kind=kind,
        base_url=base_url,
        model="claude-sonnet-4-5",
        credential_version="credential-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    store.sync_provider_profile(current)
    client = StructuredLLMClient(
        ProviderCredential(current, "anthropic-private"),
        store=store,
        transport=transport,
    )

    result = client.complete(
        workflow="market_analyst",
        response_model=AnalysisAssessment,
        system_prompt="Analyze only the supplied market facts.",
        input_payload={"price": 100_000},
        now=NOW,
    )

    assert result.stance == "bullish"
    request = requests[0]
    assert request["url"] == base_url
    assert request["headers"]["x-api-key"] == "anthropic-private"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    body = json.loads(request["body"])
    assert body["system"] == "Analyze only the supplied market facts."
    assert body["messages"][0]["role"] == "user"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
    call = store.list_model_calls()[0]
    assert call.input_tokens == 100
    assert call.output_tokens == 30
    assert call.provider_request_id == "msg-request-1"


def test_five_stage_pipeline_persists_reports_thesis_and_bounded_candidate(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")

    class FakeClient:
        model_ref = "llm-main@fingerprint"

        def complete(self, *, workflow, response_model, **kwargs):
            if workflow in {"market_analyst", "news_analyst", "sentiment_analyst"}:
                return assessment("neutral" if workflow == "news_analyst" else "bullish")
            if workflow == "research_manager":
                return ThesisAssessment(
                    summary="Evidence supports a cautious bullish intraday thesis.",
                    stance="bullish",
                    confidence=0.7,
                    key_levels={"support": 98_000, "resistance": 102_000},
                    risk_factors=("Headline reversal",),
                    horizon_minutes=240,
                )
            if workflow == "rule_generator":
                return RuleProposal(
                    parameters=RuleParameters(
                        confidence_threshold=0.88,
                        entry_quality_min=3.5,
                        toxic_flow_max=0.25,
                        stop_distance_pct=0.012,
                    ),
                    rationale="Tighten entries while the evidence is mixed.",
                )
            raise AssertionError(workflow)

    result = LLMAnalysisPipeline(FakeClient(), store).run(
        snapshot(), now=NOW, generate_rule=True
    )

    assert len(result.reports) == 3
    assert {report.analyst for report in result.reports} == {
        "market", "news", "sentiment"
    }
    assert result.thesis.stance == "bullish"
    assert result.candidate is not None
    assert result.candidate.parameters.confidence_threshold == 0.88
    assert store.latest_analyst_reports().keys() == {"market", "news", "sentiment"}
    assert store.latest_market_thesis() == result.thesis
    assert store.rule_registry()["champion_id"] == "rule-v1"
    assert store.rule_registry()["challenger_id"] is None
    assert store.rule_status(result.candidate.rule_id) == "queued"


def test_shared_analysis_produces_two_horizons_and_independent_scoped_rules(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    workflows = []

    class FakeClient:
        model_ref = "llm-main@fingerprint"

        def complete(self, *, workflow, **kwargs):
            workflows.append(workflow)
            if workflow in {"market_analyst", "news_analyst", "sentiment_analyst"}:
                return assessment("neutral" if workflow == "news_analyst" else "bullish")
            if workflow == "research_manager":
                return MarketThesisBundleAssessment(
                    intraday=HorizonThesis(
                        scope=DecisionScope.PERP_INTRADAY,
                        summary="Intraday momentum is constructive with bounded headline risk.",
                        stance="bullish",
                        confidence=0.71,
                        key_levels={"support": 98_000, "resistance": 102_000},
                        risk_factors=("Fast funding reversal",),
                        horizon_minutes=240,
                    ),
                    daily_swing=HorizonThesis(
                        scope=DecisionScope.SPOT_DAILY,
                        summary="Daily structure remains constructive above established support.",
                        stance="bullish",
                        confidence=0.68,
                        key_levels={"support": 95_000, "resistance": 110_000},
                        risk_factors=("Macro regime reversal",),
                        horizon_minutes=2_880,
                    ),
                )
            if workflow == "spot_daily_rule_generator":
                return SpotRuleProposal(
                    parameters=SpotRuleParameters(
                        entry_window=30,
                        exit_window=12,
                        atr_period=18,
                        jev_confidence_threshold=0.9,
                    ),
                    rationale="Require a slower breakout plus strong Jev confirmation.",
                )
            if workflow == "perp_intraday_rule_generator":
                return PerpRuleProposal(
                    parameters=PerpRuleParameters(confidence_threshold=0.9),
                    rationale="Raise confidence while retaining immutable hard risk limits.",
                )
            raise AssertionError(workflow)

    result = LLMAnalysisPipeline(FakeClient(), store).run_scoped(
        snapshot(),
        now=NOW,
        generate_scopes={DecisionScope.SPOT_DAILY, DecisionScope.PERP_INTRADAY},
    )

    assert result.bundle.intraday.scope == DecisionScope.PERP_INTRADAY
    assert result.bundle.daily_swing.scope == DecisionScope.SPOT_DAILY
    assert {item.scope for item in result.candidates} == {
        DecisionScope.SPOT_DAILY,
        DecisionScope.PERP_INTRADAY,
    }
    assert workflows.count("market_analyst") == 1
    assert workflows.count("research_manager") == 1
    assert store.latest_market_thesis_bundle() == result.bundle
    assert store.scoped_rule_registry(DecisionScope.SPOT_DAILY)["champion_id"] == (
        "spot-rule-v1"
    )
    assert store.scoped_rule_registry(DecisionScope.PERP_INTRADAY)["champion_id"] == (
        "perp-rule-v1"
    )
    assert store.has_open_scoped_rule_candidate(DecisionScope.SPOT_DAILY) is True
    assert store.has_open_scoped_rule_candidate(DecisionScope.PERP_INTRADAY) is True


def test_news_analyst_retries_one_structural_failure_without_repeating_peers(
    tmp_path,
):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    calls = []

    class RecoveringClient:
        model_ref = "llm-main@fingerprint"

        def complete(self, *, workflow, attempt=1, system_prompt, **kwargs):
            calls.append((workflow, attempt, system_prompt))
            if workflow == "news_analyst" and attempt == 1:
                raise StructuredLLMError("invalid_json")
            if workflow in {"market_analyst", "news_analyst", "sentiment_analyst"}:
                return assessment("neutral" if workflow == "news_analyst" else "bullish")
            if workflow == "research_manager":
                return MarketThesisBundleAssessment(
                    intraday=HorizonThesis(
                        scope=DecisionScope.PERP_INTRADAY,
                        summary="Intraday evidence remains constructive with bounded risk.",
                        stance="bullish",
                        confidence=0.7,
                        key_levels={"support": 98_000, "resistance": 102_000},
                        risk_factors=("Funding reversal",),
                        horizon_minutes=240,
                    ),
                    daily_swing=HorizonThesis(
                        scope=DecisionScope.SPOT_DAILY,
                        summary="Daily evidence remains constructive above major support.",
                        stance="bullish",
                        confidence=0.65,
                        key_levels={"support": 95_000, "resistance": 110_000},
                        risk_factors=("Macro reversal",),
                        horizon_minutes=2_880,
                    ),
                )
            raise AssertionError(workflow)

    result = LLMAnalysisPipeline(RecoveringClient(), store).run_scoped(
        snapshot(), now=NOW
    )

    assert result.bundle.thesis_id == store.latest_market_thesis_bundle().thesis_id
    assert [(workflow, attempt) for workflow, attempt, _ in calls].count(
        ("news_analyst", 1)
    ) == 1
    assert [(workflow, attempt) for workflow, attempt, _ in calls].count(
        ("news_analyst", 2)
    ) == 1
    assert [workflow for workflow, _, _ in calls].count("market_analyst") == 1
    assert [workflow for workflow, _, _ in calls].count("sentiment_analyst") == 1
    assert [workflow for workflow, _, _ in calls].count("research_manager") == 1
    retry_prompt = next(
        prompt
        for workflow, attempt, prompt in calls
        if workflow == "news_analyst" and attempt == 2
    )
    assert "recovery attempt" in retry_prompt.lower()


def test_news_analyst_does_not_retry_model_refusal(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    calls = []

    class RefusingClient:
        model_ref = "llm-main@fingerprint"

        def complete(self, *, workflow, attempt=1, **kwargs):
            calls.append((workflow, attempt))
            if workflow == "news_analyst":
                raise StructuredLLMError("model_refusal")
            return assessment()

    with pytest.raises(StructuredLLMError) as captured:
        LLMAnalysisPipeline(RefusingClient(), store).run_scoped(
            snapshot(), now=NOW
        )

    assert captured.value.code == "model_refusal"
    assert captured.value.workflow == "news_analyst"
    assert captured.value.attempts == 1
    assert calls.count(("news_analyst", 1)) == 1


def test_analysis_cycle_reports_exhausted_news_retry(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.record_snapshot(snapshot())
    secrets = ProviderSecretStore(tmp_path / "providers.toml")
    current = llm_profile()
    store.sync_provider_profile(current)
    store.record_provider_test(
        current.profile_id, status="ok", tested_at=NOW, latency_ms=1
    )
    store.activate_provider(ProviderRole.LLM, current.profile_id, actor="test", now=NOW)
    secrets.upsert(current, "private-key")

    class InvalidNewsClient:
        model_ref = "llm-main@fingerprint"

        def complete(self, *, workflow, **kwargs):
            if workflow == "news_analyst":
                raise StructuredLLMError("schema_validation")
            return assessment()

    result = run_analysis_cycle(
        store,
        secrets,
        now=NOW,
        client_factory=lambda credential: InvalidNewsClient(),
    )

    assert result == {
        "status": "degraded",
        "error_code": "schema_validation",
        "workflow": "news_analyst",
        "attempts": 2,
    }


def test_pipeline_keeps_champion_when_generated_parameters_are_unchanged(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")

    class UnchangedClient:
        model_ref = "llm-main@fingerprint"

        def complete(self, *, workflow, **kwargs):
            if workflow in {"market_analyst", "news_analyst", "sentiment_analyst"}:
                return assessment("neutral")
            if workflow == "research_manager":
                return ThesisAssessment(
                    summary="No material edge is present in the current evidence.",
                    stance="neutral",
                    confidence=0.6,
                    key_levels={"support": 98_000, "resistance": 102_000},
                    risk_factors=("Low conviction",),
                    horizon_minutes=120,
                )
            return RuleProposal(
                parameters=RuleParameters(),
                rationale="Retain the deterministic baseline.",
            )

    result = LLMAnalysisPipeline(UnchangedClient(), store).run(
        snapshot(), now=NOW, generate_rule=True
    )

    assert result.candidate is None
    assert store.rule_registry()["champion_id"] == "rule-v1"


def test_rule_generation_is_limited_to_one_successful_call_per_24_hours(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = llm_profile()
    store.sync_provider_profile(current)
    assert should_generate_rule(store, now=NOW) is True
    store.record_model_call(
        ModelCallRecord(
            call_id="rule-call-1",
            workflow="rule_generator",
            role=ProviderRole.LLM,
            profile_id=current.profile_id,
            profile_fingerprint=current.fingerprint,
            model=current.model,
            status="success",
            started_at=NOW,
            completed_at=NOW,
            latency_ms=0,
            cost_usd=0.01,
            request_hash="a" * 64,
            response_hash="b" * 64,
        )
    )

    assert should_generate_rule(store, now=NOW) is False
    assert should_generate_rule(store, now=NOW.replace(day=23) + timedelta(hours=1)) is True


def test_analysis_cycle_skips_safely_without_active_llm_or_snapshot(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    secrets = ProviderSecretStore(tmp_path / "providers.toml")

    assert run_analysis_cycle(store, secrets, now=NOW) == {
        "status": "skipped",
        "reason": "no_active_llm",
    }

    current = llm_profile()
    store.sync_provider_profile(current)
    store.record_provider_test(
        current.profile_id, status="ok", tested_at=NOW, latency_ms=10
    )
    store.activate_provider(ProviderRole.LLM, current.profile_id, actor="test", now=NOW)
    secrets.upsert(current, "private-key")

    assert run_analysis_cycle(store, secrets, now=NOW) == {
        "status": "skipped",
        "reason": "no_market_snapshot",
    }


def test_hourly_analysis_skips_unchanged_evidence_and_never_generates_rules(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    store.record_snapshot(snapshot())
    secrets = ProviderSecretStore(tmp_path / "providers.toml")
    current = llm_profile()
    store.sync_provider_profile(current)
    store.record_provider_test(current.profile_id, status="ok", tested_at=NOW, latency_ms=1)
    store.activate_provider(ProviderRole.LLM, current.profile_id, actor="test", now=NOW)
    secrets.upsert(current, "private-key")
    workflows = []

    class Client:
        model_ref = "llm-main@fingerprint"

        def complete(self, *, workflow, **kwargs):
            workflows.append(workflow)
            if workflow in {"market_analyst", "news_analyst", "sentiment_analyst"}:
                return assessment()
            if workflow == "research_manager":
                return MarketThesisBundleAssessment(
                    intraday=HorizonThesis(
                        scope=DecisionScope.PERP_INTRADAY,
                        summary="Intraday evidence remains constructive with bounded risk.",
                        stance="bullish", confidence=0.7,
                        key_levels={"support": 98_000, "resistance": 102_000},
                        risk_factors=("Funding reversal",), horizon_minutes=240,
                    ),
                    daily_swing=HorizonThesis(
                        scope=DecisionScope.SPOT_DAILY,
                        summary="Daily evidence remains constructive above major support.",
                        stance="bullish", confidence=0.65,
                        key_levels={"support": 95_000, "resistance": 110_000},
                        risk_factors=("Macro reversal",), horizon_minutes=2_880,
                    ),
                )
            raise AssertionError(f"unexpected rule call: {workflow}")

    client = Client()
    first = run_analysis_cycle(
        store, secrets, now=NOW, client_factory=lambda credential: client
    )
    second = run_analysis_cycle(
        store, secrets, now=NOW + timedelta(hours=2),
        client_factory=lambda credential: client,
    )

    assert first["status"] == "ok"
    assert first["candidate_ids"] == {}
    assert second == {"status": "skipped", "reason": "evidence_unchanged"}
    assert not any("rule_generator" in workflow for workflow in workflows)


def test_daily_model_cost_is_summed_without_hard_stopping(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    current = llm_profile()
    store.sync_provider_profile(current)
    for index, cost in enumerate((1.25, 0.90)):
        store.record_model_call(
            ModelCallRecord(
                call_id=f"cost-call-{index}",
                workflow="market_analyst",
                role=ProviderRole.LLM,
                profile_id=current.profile_id,
                profile_fingerprint=current.fingerprint,
                model=current.model,
                status="success",
                started_at=NOW + timedelta(minutes=index),
                completed_at=NOW + timedelta(minutes=index),
                latency_ms=0,
                cost_usd=cost,
                request_hash=f"{index + 1}" * 64,
                response_hash=f"{index + 3}" * 64,
            )
        )

    assert store.model_cost_since(NOW.replace(hour=0)) == 2.15
