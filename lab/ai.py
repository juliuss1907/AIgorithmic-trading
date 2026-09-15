"""Read-only AI copilot boundary; trading decisions never cross this interface."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class EvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of_utc: str
    account_id: str
    symbol: Literal["BTCUSDT"] = "BTCUSDT"
    strategy: dict
    dataset_snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    indicator_evidence: dict[str, float | bool | None]
    target_weight: float = Field(ge=0, le=.5)
    account_status: Literal["active", "halted"]
    drawdown: float = Field(ge=-1, le=0)
    latest_cycle_id: str | None
    recent_fills: tuple[dict, ...] = ()


class AIProvider(Protocol):
    name: str
    enabled: bool

    def explain(self, evidence: EvidencePacket) -> str: ...


class DisabledAIProvider:
    name = "not-configured"
    enabled = False

    def explain(self, evidence):
        del evidence
        raise RuntimeError("AI provider is not configured")


class CopilotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["available", "disabled"]
    provider: str
    text: str | None
    advisory_only: Literal[True] = True


class Copilot:
    def __init__(self, provider: AIProvider | None = None):
        self.provider = provider or DisabledAIProvider()

    def explain(self, evidence: EvidencePacket):
        if not self.provider.enabled:
            return CopilotResponse(
                status="disabled", provider=self.provider.name, text=None,
            )
        text = self.provider.explain(evidence)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("AI provider returned no explanation")
        text = text.strip()
        if len(text) > 4_000:
            raise ValueError("AI explanation exceeds 4,000 characters")
        return CopilotResponse(
            status="available", provider=self.provider.name, text=text,
        )
