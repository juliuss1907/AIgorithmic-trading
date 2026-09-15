import pytest
from pydantic import ValidationError

import pandas as pd

from lab.ai import Copilot, DisabledAIProvider, EvidencePacket, build_evidence_packet
from lab.paper import PaperTradingService


def packet():
    return EvidencePacket.model_validate({
        "as_of_utc": "2026-08-31T00:02:00+00:00",
        "account_id": "paper-1", "symbol": "BTCUSDT",
        "strategy": {"family": "sma_crossover", "fast_window": 20, "slow_window": 50},
        "dataset_snapshot_id": "a" * 64,
        "indicator_evidence": {"close": 60000, "sma_fast": 61000, "sma_slow": 59000},
        "target_weight": .5, "account_status": "active", "drawdown": -.03,
        "latest_cycle_id": "cycle-1", "recent_fills": [],
    })


def test_evidence_packet_rejects_execution_fields():
    payload = packet().model_dump()
    payload["order_quantity"] = 1
    with pytest.raises(ValidationError):
        EvidencePacket.model_validate(payload)


def test_disabled_provider_is_explicit():
    result = Copilot(DisabledAIProvider()).explain(packet())
    assert result.status == "disabled"
    assert result.text is None
    assert result.advisory_only is True


def test_provider_receives_read_only_evidence_and_returns_advisory_text():
    class Provider:
        name = "test-provider"
        enabled = True

        def explain(self, evidence):
            assert isinstance(evidence, EvidencePacket)
            return "SMA nhanh đang cao hơn SMA chậm."

    result = Copilot(Provider()).explain(packet())
    assert result.status == "available"
    assert result.provider == "test-provider"
    assert result.text.startswith("SMA nhanh")
    assert result.advisory_only is True


def test_provider_output_is_bounded():
    class Provider:
        name = "verbose"
        enabled = True

        def explain(self, evidence):
            return "x" * 5000

    with pytest.raises(ValueError, match="4,000"):
        Copilot(Provider()).explain(packet())


def test_evidence_packet_is_built_from_persisted_paper_evidence(tmp_path):
    service = PaperTradingService(tmp_path / "paper.sqlite3")
    account = service.create_account(
        {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        dataset_snapshot_id="a" * 64,
    )
    index = pd.date_range("2025-01-01", periods=2)
    close = pd.Series([100., 120], index=index)
    frame = pd.DataFrame({
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": 1,
    }, index=index)
    service.run_cycle(
        account["id"], frame, bid=100, ask=101, dataset_snapshot_id="b" * 64,
    )

    evidence = build_evidence_packet(service, account["id"])

    assert evidence.dataset_snapshot_id == "b" * 64
    assert evidence.latest_cycle_id is not None
    assert evidence.indicator_evidence["sma_fast"] == 120
    assert len(evidence.recent_fills) == 1
