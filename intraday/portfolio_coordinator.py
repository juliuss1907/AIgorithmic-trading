"""Deterministic parent risk coordinator for spot and isolated-perp paper sleeves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from intraday.contracts import DecisionScope


class ParentPortfolioState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_equity: float = Field(default=10_000, gt=0)
    realized_pnl: float = 0
    fees: float = Field(default=0, ge=0)
    funding: float = 0
    spot_quantity: float = Field(default=0, ge=0)
    spot_entry_price: float | None = Field(default=None, gt=0)
    perp_quantity: float = 0
    perp_entry_price: float | None = Field(default=None, gt=0)
    mark_price: float = Field(gt=0)
    spot_price: float = Field(gt=0)
    perp_mark_price: float = Field(gt=0)
    day_start_equity: float = Field(gt=0)
    high_water_mark: float = Field(gt=0)
    entries_paused: bool = True
    halt_reason: str | None = Field(default=None, max_length=120)
    paper_active: bool = False
    soak_evaluation_id: str | None = Field(default=None, max_length=128)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def updated_at_is_aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        return value

    @model_validator(mode="before")
    @classmethod
    def seed_scope_prices_from_legacy_mark(cls, value):
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        legacy = payload.get("mark_price") or payload.get("perp_mark_price")
        if legacy is not None:
            payload.setdefault("spot_price", legacy)
            payload.setdefault("perp_mark_price", legacy)
            payload.setdefault("mark_price", payload["perp_mark_price"])
        return payload

    @model_validator(mode="after")
    def positions_have_entry_prices(self):
        if (self.spot_quantity == 0) != (self.spot_entry_price is None):
            raise ValueError("spot entry price must match position state")
        if (self.perp_quantity == 0) != (self.perp_entry_price is None):
            raise ValueError("perp entry price must match position state")
        if self.mark_price != self.perp_mark_price:
            raise ValueError("legacy mark_price must match perp_mark_price")
        return self

    @property
    def spot_notional(self) -> float:
        return self.spot_quantity * self.spot_price

    @property
    def perp_notional(self) -> float:
        return self.perp_quantity * self.perp_mark_price

    @property
    def equity(self) -> float:
        spot_unrealized = (
            self.spot_quantity * (self.spot_price - self.spot_entry_price)
            if self.spot_entry_price is not None
            else 0
        )
        perp_unrealized = (
            self.perp_quantity * (self.perp_mark_price - self.perp_entry_price)
            if self.perp_entry_price is not None
            else 0
        )
        return (
            self.initial_equity
            + self.realized_pnl
            + spot_unrealized
            + perp_unrealized
            - self.fees
            - self.funding
        )

    @property
    def daily_return(self) -> float:
        return self.equity / self.day_start_equity - 1

    @property
    def drawdown(self) -> float:
        return self.equity / self.high_water_mark - 1


class PortfolioAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: DecisionScope
    allowed: bool
    target_notional: float
    reduce_only: bool
    flatten_required: bool = False
    reason_codes: tuple[str, ...] = ()
    projected_gross_exposure_pct: float = Field(ge=0)
    projected_net_delta_pct: float = Field(ge=0)
    projected_isolated_margin_pct: float = Field(ge=0)


@dataclass(frozen=True)
class ParentPortfolioPolicy:
    initial_equity: float = 10_000
    spot_budget_pct: float = 0.60
    perp_budget_pct: float = 0.40
    spot_sleeve_target_pct: float = 0.50
    perp_sleeve_notional_pct: float = 0.50
    max_gross_exposure_pct: float = 0.50
    max_abs_net_delta_pct: float = 0.50
    max_isolated_margin_pct: float = 0.10
    leverage: int = 3
    daily_loss_limit_pct: float = 0.015
    max_drawdown_pct: float = 0.08


class ParentPortfolioCoordinator:
    def __init__(self, policy: ParentPortfolioPolicy | None = None):
        self.policy = policy or ParentPortfolioPolicy()

    def authorize_target(
        self,
        state: ParentPortfolioState,
        *,
        scope: DecisionScope,
        target_notional: float,
    ) -> PortfolioAuthorization:
        equity = state.equity
        current = (
            state.spot_notional
            if scope == DecisionScope.SPOT_DAILY
            else state.perp_notional
        )
        target = float(target_notional)
        if scope == DecisionScope.SPOT_DAILY and target < 0:
            target = 0

        if (
            scope == DecisionScope.PERP_INTRADAY
            and current
            and target
            and (current > 0) != (target > 0)
        ):
            return self._result(
                state,
                scope=scope,
                target=0,
                allowed=True,
                reduce_only=True,
                reasons=("no_same_tick_flip",),
            )

        reducing = abs(target) < abs(current) or target == 0
        if state.drawdown <= -self.policy.max_drawdown_pct:
            return self._result(
                state,
                scope=scope,
                target=0,
                allowed=reducing and target == 0,
                reduce_only=True,
                reasons=("parent_drawdown_limit",),
                flatten_required=True,
            )

        entry_reasons = []
        if not reducing:
            if not state.paper_active:
                entry_reasons.append("paper_not_active")
            if state.entries_paused:
                entry_reasons.append(state.halt_reason or "entries_paused")
            if state.daily_return <= -self.policy.daily_loss_limit_pct:
                entry_reasons.append("daily_loss_limit")

        spot_target = target if scope == DecisionScope.SPOT_DAILY else state.spot_notional
        perp_target = target if scope == DecisionScope.PERP_INTRADAY else state.perp_notional
        spot_limit = (
            equity
            * self.policy.spot_budget_pct
            * self.policy.spot_sleeve_target_pct
        )
        perp_limit = (
            equity
            * self.policy.perp_budget_pct
            * self.policy.perp_sleeve_notional_pct
        )
        if (
            scope == DecisionScope.SPOT_DAILY
            and spot_target > spot_limit + 1e-9
        ):
            entry_reasons.append("spot_sleeve_limit")
        if (
            scope == DecisionScope.PERP_INTRADAY
            and abs(perp_target) > perp_limit + 1e-9
        ):
            entry_reasons.append("perp_sleeve_limit")

        gross = (spot_target + abs(perp_target)) / equity
        net = abs(spot_target + perp_target) / equity
        margin = abs(perp_target) / self.policy.leverage / equity
        if gross > self.policy.max_gross_exposure_pct + 1e-9:
            entry_reasons.append("gross_exposure_limit")
        if net > self.policy.max_abs_net_delta_pct + 1e-9:
            entry_reasons.append("net_delta_limit")
        if margin > self.policy.max_isolated_margin_pct + 1e-9:
            entry_reasons.append("isolated_margin_limit")

        reasons = tuple(dict.fromkeys(entry_reasons))
        return PortfolioAuthorization(
            scope=scope,
            allowed=not reasons,
            target_notional=target if not reasons else current,
            reduce_only=reducing,
            reason_codes=reasons,
            projected_gross_exposure_pct=max(0, gross),
            projected_net_delta_pct=max(0, net),
            projected_isolated_margin_pct=max(0, margin),
        )

    def _result(
        self,
        state: ParentPortfolioState,
        *,
        scope: DecisionScope,
        target: float,
        allowed: bool,
        reduce_only: bool,
        reasons: tuple[str, ...],
        flatten_required: bool = False,
    ) -> PortfolioAuthorization:
        spot = target if scope == DecisionScope.SPOT_DAILY else state.spot_notional
        perp = target if scope == DecisionScope.PERP_INTRADAY else state.perp_notional
        equity = state.equity
        return PortfolioAuthorization(
            scope=scope,
            allowed=allowed,
            target_notional=target,
            reduce_only=reduce_only,
            flatten_required=flatten_required,
            reason_codes=reasons,
            projected_gross_exposure_pct=(spot + abs(perp)) / equity,
            projected_net_delta_pct=abs(spot + perp) / equity,
            projected_isolated_margin_pct=(
                abs(perp) / self.policy.leverage / equity
            ),
        )


def apply_operator_command(
    state: ParentPortfolioState, command: str, *, now: datetime
) -> ParentPortfolioState:
    if command == "pause":
        return state.model_copy(
            update={
                "entries_paused": True,
                "halt_reason": "operator_pause",
                "updated_at": now,
            }
        )
    if command == "resume":
        if state.drawdown <= -0.08:
            raise ValueError("cannot resume while parent drawdown limit is breached")
        return state.model_copy(
            update={
                "entries_paused": False,
                "halt_reason": None,
                "updated_at": now,
            }
        )
    if command != "flatten":
        raise ValueError("unsupported parent portfolio command")
    realized = state.realized_pnl
    if state.spot_entry_price is not None:
        realized += state.spot_quantity * (state.spot_price - state.spot_entry_price)
    if state.perp_entry_price is not None:
        realized += state.perp_quantity * (
            state.perp_mark_price - state.perp_entry_price
        )
    return state.model_copy(
        update={
            "realized_pnl": realized,
            "spot_quantity": 0,
            "spot_entry_price": None,
            "perp_quantity": 0,
            "perp_entry_price": None,
            "entries_paused": True,
            "halt_reason": "operator_flatten",
            "updated_at": now,
        }
    )
