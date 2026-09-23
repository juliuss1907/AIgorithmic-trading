"""Observation-only decision experiments; this module has no execution dependency."""

from __future__ import annotations

import hashlib
from datetime import datetime

from intraday.contracts import DecisionMode, DecisionScope, StateVariant
from intraday.journal import record_scoped_signal


def make_experiment_pair_id(scope, snapshot, scheduled_for: datetime) -> str:
    return hashlib.sha256(
        f"{scope.value}:{snapshot.checksum}:{scheduled_for.isoformat()}".encode()
    ).hexdigest()[:32]


def record_compact_shadow(
    store,
    provider,
    snapshot,
    *,
    scope: DecisionScope,
    rule_id: str,
    experiment_pair_id: str,
    now: datetime,
) -> int:
    scoped = provider.decide_scoped(
        snapshot,
        f"{snapshot.symbol}:{scope.value}:compact:{int(now.timestamp() * 1000)}",
        scope,
        now,
        state_variant=StateVariant.COMPACT_V1,
        decision_mode=DecisionMode.SHADOW,
        experiment_pair_id=experiment_pair_id,
    )
    if (
        scoped.state_variant != StateVariant.COMPACT_V1
        or scoped.decision_mode != DecisionMode.SHADOW
        or scoped.experiment_pair_id != experiment_pair_id
    ):
        raise ValueError("provider did not preserve the compact shadow contract")
    return record_scoped_signal(
        store,
        snapshot,
        scoped,
        gate_passed=False,
        gate_reason="shadow_observation_only",
        rule_id=rule_id,
    )
