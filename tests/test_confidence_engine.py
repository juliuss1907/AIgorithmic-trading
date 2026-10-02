from datetime import timedelta
from dataclasses import replace

import pytest

from intraday.contracts import Direction
from intraday.replay_v2.confidence import training_frontier, review_confidence
from intraday.replay_v2.confidence_contracts import ConfidenceProposal
from intraday.replay_v2.confidence_reviews import ReviewStore
from intraday.replay_v2.contracts import FundingHistory, binance_gate_profile
from test_replay_v2_perp import NOW, inputs, quote, decision


def prepared():
    quotes = [quote(i*30, 100.4 if i % 4 == 3 else 100) for i in range(601)]
    decisions = [decision(i*30, direction=(Direction.TAKE_PROFIT if i % 4 == 2 else Direction.BUY),
                 confidence=.744) for i in range(600) if i % 4 in (0, 2)]
    history = FundingHistory(symbol="ETH", source="test", coverage_start=NOW,
        coverage_end=NOW+timedelta(days=1), settlements=[])
    cfg, data = inputs(quotes, decisions, profile=binance_gate_profile().model_copy(update={"funding":history}))
    cfg = cfg.model_copy(update={"end":NOW+timedelta(seconds=18001)})
    return cfg, data, {"coverage":1.0,"raw_decisions":300,"source_checksum":"a"*64}


class Client:
    model_ref = "test/fake"
    def __init__(self):
        self.calls = []
    def complete(self, **kw):
        self.calls.append(kw)
        return ConfidenceProposal(status="proposed", confidence_threshold=.732,
                                  rationale="Training trades support this fractional threshold.")


def test_llm_sees_training_only_and_exact_fraction_is_replayed(tmp_path, monkeypatch):
    cfg, data, evidence = prepared()
    monkeypatch.setattr("intraday.replay_v2.confidence.prepare_perp", lambda *a,**k:(cfg,data,evidence))
    client = Client()
    result = review_confidence("unused", "ETH", tmp_path, now=cfg.end, client=client, collect_funding=False)
    assert result["proposed_threshold"] == .732
    assert result["holdout"]["parameters"]["confidence_threshold"] == .732
    assert len(client.calls) == 1
    payload = client.calls[0]["input_payload"]
    assert "holdout" not in payload
    assert payload["window"]["end"] == result["training_end"]
    assert all(row["parameters"]["confidence_threshold"] >= .69 for row in payload["frontier"])
    assert result["status"] == "deferred"  # Less than14days, never a formal pass.
    again = review_confidence("unused", "ETH", tmp_path, now=cfg.end, client=client, collect_funding=False)
    assert again["review_id"] == result["review_id"]
    assert len(client.calls) == 1


def test_insufficient_data_never_calls_model_or_assigns_threshold(tmp_path, monkeypatch):
    cfg, data, evidence = prepared()
    data = replace(data, decisions=data.decisions[:10])
    monkeypatch.setattr("intraday.replay_v2.confidence.prepare_perp", lambda *a,**k:(cfg,data,evidence))
    client = Client()
    result = review_confidence("unused", "ETH", tmp_path, now=cfg.end, client=client, collect_funding=False)
    assert result["status"] == "deferred"
    assert result["proposed_threshold"] is None
    assert client.calls == []


def test_model_failure_is_durable_and_does_not_leak_error_content(tmp_path, monkeypatch):
    cfg, data, evidence = prepared()
    monkeypatch.setattr("intraday.replay_v2.confidence.prepare_perp", lambda *a,**k:(cfg,data,evidence))
    class Broken(Client):
        def complete(self, **kw):
            self.calls.append(kw)
            raise RuntimeError("PRIVATE-DO-NOT-ECHO")
    client = Broken()
    result = review_confidence("unused", "ETH", tmp_path, now=cfg.end, client=client, collect_funding=False)
    assert result["status"] == "error"
    assert "PRIVATE" not in str(result)
    review_confidence("unused", "ETH", tmp_path, now=cfg.end, client=client, collect_funding=False)
    assert len(client.calls) == 1


def test_missing_provider_secret_is_deferred_without_silent_provider_switch(tmp_path, monkeypatch):
    from intraday.llm_pipeline import StructuredLLMError
    cfg, data, evidence = prepared()
    monkeypatch.setattr("intraday.replay_v2.confidence.prepare_perp", lambda *a,**k:(cfg,data,evidence))
    def missing():
        raise StructuredLLMError("missing_secret")
    result = review_confidence("unused", "ETH", tmp_path, now=cfg.end,
                               client_factory=missing, collect_funding=False)
    assert result["status"] == "deferred"
    assert result["blockers"] == ["llm_provider:missing_secret"]
    assert result["proposed_threshold"] is None
    assert result["last_model_training_end"] is None
