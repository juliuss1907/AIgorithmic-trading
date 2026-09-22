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
    indicator_evidence: dict[str, float | bool | str | None]
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


def build_evidence_packet(paper_service, account_id):
    account = paper_service.get_account(account_id)
    cycles = paper_service.list_cycles(account_id)
    latest = cycles[0] if cycles else None
    fills = paper_service.list_fills(account_id)[-10:]
    return EvidencePacket(
        as_of_utc=latest["created_at"] if latest else account["updated_at"],
        account_id=account_id,
        strategy=account["strategy"],
        dataset_snapshot_id=(
            latest["dataset_snapshot_id"] if latest else account["dataset_snapshot_id"]
        ),
        indicator_evidence=latest["evidence"] if latest else {},
        target_weight=latest["signal"] if latest else 0,
        account_status=account["status"],
        drawdown=account["drawdown"],
        latest_cycle_id=latest["id"] if latest else None,
        recent_fills=tuple(fills),
    )
