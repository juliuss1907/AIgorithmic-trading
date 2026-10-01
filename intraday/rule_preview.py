"""Pure gate calculations shared by read projections and explicit evaluations."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuleGatePreview:
    # These values become an evaluation only in the explicit mutating command.
    values: dict[str, Any]
    blockers: tuple[str, ...]
    hard_risk_violations: int
