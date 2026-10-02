"""Research-only shared Spot cash book. Never bridges to runtime authorization."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from intraday.replay_v2.contracts import FrozenModel, positive_policy_number, utc


ZERO, ONE = Decimal(0), Decimal(1)
WEIGHTS = {"BTCUSDT": Decimal(".50"), "ETHUSDT": Decimal(".25"), "SOLUSDT": Decimal(".25")}
CAP = Decimal(".60")
FEE, SLIP, STOP = Decimal(".001"), Decimal(".0005"), Decimal(".10")


class PortfolioConfig(FrozenModel):
    start: datetime
    end: datetime
    capital: Decimal = Field(default=Decimal(1000), gt=0, le=10000)
    daily_loss: Decimal = Field(default=Decimal(".03"), gt=0, lt=1)
    max_drawdown: Decimal = Field(default=Decimal(".10"), gt=0, lt=1)
    exit_window: Literal[6, 8, 10] = 8
    trend_filter: bool = False

    _times = field_validator("start", "end")(utc)
    _numbers = field_validator("capital", "daily_loss", "max_drawdown")(positive_policy_number)

    @model_validator(mode="after")
    def window(self):
        if self.end <= self.start or any(int(t.timestamp()) % 14400 or t.microsecond for t in (self.start, self.end)):
            raise ValueError("portfolio window must use ordered UTC 4h boundaries")
        if self.trend_filter and self.exit_window != 8:
            raise ValueError("trend variant requires exit 8")
        return self


@dataclass
class Position:
    quantity: Decimal = ZERO
    entry_price: Decimal = ZERO
    entry_fee: Decimal = ZERO
    entry_slip: Decimal = ZERO
    entered_at: datetime | None = None


class PortfolioBook:
    def __init__(self, config):
        self.config = config
        self.cash = self.peak = self.day_start = self.last_equity = config.capital
        self.day = config.start.date()
        self.positions = {symbol: Position() for symbol in sorted(WEIGHTS)}
        self.events, self.trades, self.curve = [], [], []
        self.fees = self.slippage = self.max_drawdown = self.max_exposure = ZERO
        self.halted, self.halt_reason = False, None

    def equity(self, marks):
        if set(marks) != set(WEIGHTS) or any(not p.is_finite() or p <= 0 for p in marks.values()):
            raise ValueError("all three finite positive marks are required")
        return self.cash + sum(self.positions[s].quantity * marks[s] for s in WEIGHTS)

    def event(self, at, kind, reason, **values):
        self.events.append({"at": utc(at).isoformat(), "kind": kind, "reason": reason, **values})

    def observe(self, at, marks, *, stage="risk"):
        equity = self.equity(marks)
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, ONE - equity / self.peak)
        exposure = sum(self.positions[s].quantity * marks[s] for s in WEIGHTS)
        self.max_exposure = max(self.max_exposure, exposure / equity if equity > 0 else ZERO)
        self.last_equity = equity
        self.curve.append({"at": utc(at).isoformat(), "stage": stage,
                           "equity_known": str(equity), "cash": str(self.cash),
                           "exposure": str(exposure)})
        return equity

    def advance_day(self, at):
        at = utc(at)
        if at.date() < self.day:
            raise ValueError("portfolio time cannot go backwards")
        if at.date() == self.day:
            return
        self.day, self.day_start = at.date(), self.last_equity
        if self.halt_reason == "daily_loss_limit":
            if any(p.quantity for p in self.positions.values()):
                raise ValueError("daily resume requires a flat portfolio")
            if self.last_equity <= self.peak * (ONE - self.config.max_drawdown):
                self.halt_reason = "max_drawdown"
                self.event(at, "halt", self.halt_reason, upgraded_from="daily_loss_limit")
            else:
                self.halted, self.halt_reason = False, None
                self.event(at, "resume", "next_utc_day")

    def enter_batch(self, at, marks, multipliers):
        equity = self.equity(marks)
        if not set(multipliers) <= set(WEIGHTS) or any(
            not m.is_finite() or not ZERO <= m <= ONE for m in multipliers.values()
        ):
            raise ValueError("invalid entry multipliers")
        if self.halted:
            for symbol in sorted(multipliers):
                self.event(at, "entry_blocked", "portfolio_loss_limit", symbol=symbol)
            return
        base = min(self.config.capital, equity)
        exposure = sum(self.positions[s].quantity * marks[s] for s in WEIGHTS)
        requests = {s: base * CAP * WEIGHTS[s] * multipliers[s]
                    for s in sorted(multipliers) if not self.positions[s].quantity and multipliers[s] > 0}
        total = sum(requests.values(), ZERO)
        if not total:
            return
        available = max(ZERO, min(base * CAP - exposure, self.cash / (ONE + FEE + SLIP)))
        scale = min(ONE, available / total)
        for symbol, requested in requests.items():
            notional = requested * scale
            if not notional:
                self.event(at, "entry_blocked", "allocation_limit", symbol=symbol)
                continue
            price = marks[symbol]
            quantity = notional / price
            if quantity * price > notional:
                quantity = quantity.next_minus()
            notional = quantity * price
            fee, slip = notional * FEE, notional * SLIP
            self.cash -= notional + fee + slip
            self.fees += fee
            self.slippage += slip
            self.positions[symbol] = Position(quantity, price, fee, slip, at)
            self.event(at, "entry", "donchian_entry", symbol=symbol, price=str(price),
                       quantity=str(quantity), notional=str(notional), cash=str(self.cash),
                       batch_base_equity=str(base), requested_notional=str(requested),
                       pre_batch_exposure=str(exposure), allocation_scale=str(scale))
        if self.cash < 0:
            raise ValueError("portfolio cash conservation failed")

    def close(self, symbol, at, price, reason):
        position = self.positions[symbol]
        if not position.quantity:
            return
        notional = position.quantity * price
        fee, slip = notional * FEE, notional * SLIP
        gross = position.quantity * (price - position.entry_price)
        self.cash += notional - fee - slip
        self.fees += fee
        self.slippage += slip
        self.trades.append({"symbol": symbol, "opened_at": position.entered_at.isoformat(),
                           "closed_at": utc(at).isoformat(), "entry_price": str(position.entry_price),
                           "exit_price": str(price), "quantity": str(position.quantity),
                           "gross_pnl": gross, "exchange_fee": position.entry_fee + fee,
                           "slippage_cost": position.entry_slip + slip,
                           "net_pnl": gross - position.entry_fee - position.entry_slip - fee - slip,
                           "exit_reason": reason})
        self.positions[symbol] = Position()
        self.event(at, "exit", reason, symbol=symbol, price=str(price), cash=str(self.cash))

    def enforce_risk(self, at, marks):
        equity = self.observe(at, marks)
        dd_breached = equity <= self.peak * (ONE - self.config.max_drawdown)
        daily_breached = equity <= self.day_start * (ONE - self.config.daily_loss)
        if self.halt_reason == "max_drawdown" or not (dd_breached or daily_breached):
            return
        reason = "max_drawdown" if dd_breached else "daily_loss_limit"
        if self.halted and self.halt_reason == reason:
            return
        self.halted, self.halt_reason = True, reason
        for symbol in sorted(WEIGHTS):
            self.close(symbol, at, marks[symbol], reason)
        equity = self.observe(at, marks, stage="risk_flatten")
        if equity <= self.peak * (ONE - self.config.max_drawdown):
            self.halt_reason = "max_drawdown"
        self.event(at, "halt", self.halt_reason, trigger_reason=reason,
                   equity_known=str(equity))
