"""Research-only confidence proposals; no execution or registry authority."""

from typing import Literal

from pydantic import Field, model_validator

from intraday.contracts import StrictContract


class ConfidenceProposal(StrictContract):
    status: Literal["proposed", "insufficient_data"]
    confidence_threshold: float | None = Field(default=None, ge=.69, le=1.0, allow_inf_nan=False)
    rationale: str = Field(min_length=20, max_length=2000)

    @model_validator(mode="after")
    def threshold_matches_status(self):
        if (self.status == "proposed") != (self.confidence_threshold is not None):
            raise ValueError("only a proposed confidence may contain a threshold")
        return self
