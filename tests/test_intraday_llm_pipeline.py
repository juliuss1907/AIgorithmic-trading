import json
from datetime import datetime, timezone

from intraday.contracts import (
    AnalysisAssessment,
    AnalystReport,
    FeatureSnapshot,
    MarketThesis,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
    RuleParameters,
    RuleProposal,
    ThesisAssessment,
)
from intraday.llm_pipeline import LLMAnalysisPipeline, StructuredLLMClient
from intraday.provider_client import HttpResponse
from intraday.provider_profiles import ProviderCredential
from intraday.store import IntradayStore


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
    assert body["tools"] == []
    call = store.list_model_calls()[0]
    assert call.workflow == "market_analyst"
    assert call.status == "success"
    assert call.cost_usd == 0.002
    assert "private-llm-key" not in call.model_dump_json()


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
