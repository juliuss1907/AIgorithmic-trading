"""Research-only shared Spot/isolated Perp ledger, without runtime writes."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from intraday.replay_v2.portfolio_book import FEE, SLIP, ONE, ZERO, PortfolioBook, PortfolioConfig, Position


PERP_FEE, PERP_SLIP = Decimal(".0005"), Decimal(".0005")
FIVE_WEIGHTS = {"BTCUSDT": ".40", "ETHUSDT": ".20", "SOLUSDT": ".20",
                "NEARUSDT": ".10", "ZECUSDT": ".10"}


class MixedConfig(PortfolioConfig):
    weights: dict[str, Decimal] = Field(default_factory=lambda: {s: Decimal(w) for s, w in FIVE_WEIGHTS.items()})
    trend_filter: bool = True
    perp_weights: dict[str, Decimal] = Field(default_factory=lambda: {"BTCUSDT": Decimal(".50"), "ETHUSDT": Decimal(".50")})
    perp_cap: Decimal = Field(default=Decimal(".30"), gt=0, le=1)
    reserve: Decimal = Field(default=Decimal(".10"), gt=0, lt=1)
    leverage: int = Field(default=3, ge=1, le=10, strict=True)
    include_perp: bool = True

    _perp_weights = field_validator("perp_weights")(PortfolioConfig.valid_weights.__func__)

    @model_validator(mode="after")
    def budgets(self):
        if self.entry_cap + self.perp_cap + self.reserve > ONE:
            raise ValueError("Spot cap + Perp notional cap + reserve must not exceed one")
        return self


@dataclass
class PerpPosition(Position):
    margin: Decimal = ZERO
    stop: Decimal = ZERO
    funding_paid: Decimal = ZERO


class MixedBook(PortfolioBook):
    def __init__(self, config):
        super().__init__(config)
        self.perps = {s: PerpPosition() for s in sorted(config.perp_weights)}
        self.perp_marks = {}
        self.funding_paid = self.max_margin = self.max_perp_exposure = ZERO
        self.pause_until = None
        self.limitations = set()

    @property
    def locked_margin(self):
        return sum((p.margin for p in self.perps.values()), ZERO)

    @property
    def free_cash(self):
        return self.cash - self.locked_margin

    @property
    def flat(self):
        return not any(p.quantity for p in (*self.positions.values(), *self.perps.values()))

    def equity(self, marks):
        spot_equity = super().equity(marks)
        pnl = ZERO
        for s, p in self.perps.items():
            if p.quantity:
                mark = self.perp_marks.get(s)
                if mark is None or not mark.is_finite() or mark <= 0:
                    raise ValueError("open Perp positions require finite positive marks")
                pnl += p.quantity * (mark-p.entry_price)
        return spot_equity + pnl

    def exposures(self, marks):
        spot = sum((self.positions[s].quantity * marks[s] for s in self.weights), ZERO)
        perp = sum((abs(p.quantity) * self.perp_marks[s] for s, p in self.perps.items() if p.quantity), ZERO)
        return spot, perp

    def observe(self, at, marks, *, stage="risk"):
        equity = self.equity(marks)
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, ONE-equity/self.peak)
        spot, perp = self.exposures(marks)
        if equity > 0:
            self.max_exposure = max(self.max_exposure, (spot+perp)/equity)
            self.max_perp_exposure = max(self.max_perp_exposure, perp/equity)
            self.max_margin = max(self.max_margin, self.locked_margin/equity)
        self.last_equity = equity
        self.curve.append({"at": at.isoformat(), "stage": stage, "equity_known": str(equity),
            "cash": str(self.cash), "free_cash": str(self.free_cash), "spot_notional": str(spot),
            "perp_gross_notional": str(perp), "locked_margin": str(self.locked_margin)})
        return equity

    def advance_day(self, at):
        if at.date() < self.day:
            raise ValueError("portfolio time cannot go backwards")
        if at.date() != self.day:
            self.day, self.day_start = at.date(), self.last_equity

    @property
    def terminal_risk_halted(self):
        return self.halt_reason in {"max_drawdown", "unsupported_liquidation"}

    def capital_stop_reason(self, equity):
        return "max_drawdown" if equity <= self.peak*(ONE-self.config.max_drawdown) else None

    def maybe_resume(self, at, marks):
        if self.halt_reason != "daily_loss_limit" or at < self.pause_until or not self.flat:
            return
        equity = self.equity(marks)
        reason = self.capital_stop_reason(equity)
        if reason:
            self.halt_reason = reason
            self.event(at, "halt", reason, upgraded_from="daily_loss_limit")
        else:
            self.halted, self.halt_reason = False, None
            self.day_start = equity
            self.event(at, "resume", "next_utc_day_and_flat")

    def enforce_risk(self, at, marks):
        equity = self.observe(at, marks)
        if self.terminal_risk_halted:
            return
        reason = self.capital_stop_reason(equity) or (
            "daily_loss_limit" if equity <= self.day_start*(ONE-self.config.daily_loss) else None)
        if reason and reason != self.halt_reason:
            self.halted, self.halt_reason = True, reason
            self.pause_until = at.replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1)
            self.event(at, "halt", reason, equity_known=str(equity))

    def allocation_base(self, marks):
        return min(self.config.capital, self.equity(marks))

    def spot_budget(self, marks):
        return self.allocation_base(marks)*self.config.entry_cap

    def perp_budget(self, marks):
        return self.allocation_base(marks)*self.config.perp_cap

    def combined_budget(self, marks):
        return self.allocation_base(marks)*(self.config.entry_cap+self.config.perp_cap)

    def reserve_floor(self, marks):
        return self.allocation_base(marks)*self.config.reserve

    def budget_exposures(self, marks):
        return self.exposures(marks)

    def enter_batch(self, at, marks, multipliers):
        if not set(multipliers) <= set(self.weights) or any(
            not m.is_finite() or not ZERO <= m <= ONE for m in multipliers.values()
        ):
            raise ValueError("invalid entry multipliers")
        if self.halted:
            for s in sorted(multipliers):
                self.event(at, "entry_blocked", "portfolio_loss_limit", symbol=s, market="spot")
            return
        base = self.allocation_base(marks)
        spot, perp = self.budget_exposures(marks)
        budget = self.spot_budget(marks)
        requests = {s: budget*self.weights[s]*multipliers[s]
                    for s in sorted(multipliers) if not self.positions[s].quantity and multipliers[s] > 0}
        total = sum(requests.values(), ZERO)
        if not total:
            return
        room = max(ZERO, min(budget-spot,
            self.combined_budget(marks)-spot-perp,
            (self.free_cash-self.reserve_floor(marks))/(ONE+FEE+SLIP)))
        scale = min(ONE, room/total)
        for s, requested in requests.items():
            notional = requested*scale
            if notional <= 0:
                self.event(at, "entry_blocked", "allocation_or_reserve", symbol=s, market="spot")
                continue
            quantity = notional/marks[s]
            if quantity*marks[s] > notional:
                quantity = quantity.next_minus()
            notional = quantity*marks[s]
            fee, slip = notional*FEE, notional*SLIP
            self.cash -= notional+fee+slip
            self.fees += fee
            self.slippage += slip
            self.positions[s] = Position(quantity, marks[s], fee, slip, at)
            self.event(at, "entry", "donchian_entry", symbol=s, market="spot", price=str(marks[s]),
                notional=str(notional), quantity=str(quantity), base_equity=str(base),
                requested_notional=str(requested), allocation_scale=str(scale))

    def close(self, symbol, at, price, reason):
        if self.positions[symbol].quantity:
            super().close(symbol, at, price, reason)
            self.trades[-1].update(market="spot", side="long", funding_paid=ZERO)
            self.events[-1]["market"] = "spot"

    def enter_perp(self, symbol, at, marks, price, side, stop_distance, *, approved_target=None):
        if symbol not in self.perps or side not in {-1, 1} or not ZERO < stop_distance < ONE or not price.is_finite() or price <= 0:
            raise ValueError("invalid Perp entry")
        if self.halted or not self.config.include_perp or self.perps[symbol].quantity:
            return False
        base, requested, target = self.perp_target(symbol, marks)
        if approved_target is not None:
            target = min(target, approved_target)
        if target <= 0:
            self.event(at, "entry_blocked", "allocation_or_reserve", symbol=symbol, market="perp")
            return False
        quantity = target/price
        if quantity*price > target:
            quantity = quantity.next_minus()
        target = quantity*price
        fee, slip = target*PERP_FEE, target*PERP_SLIP
        self.cash -= fee+slip
        self.fees += fee
        self.slippage += slip
        self.perps[symbol] = PerpPosition(quantity*side, price, fee, slip, at,
            target/self.config.leverage, price*(ONE-side*stop_distance), ZERO)
        self.event(at, "entry", "recorded_jev", symbol=symbol, market="perp", side=side,
            notional=str(target), price=str(price), quantity=str(quantity), base_equity=str(base),
            requested_notional=str(requested), locked_margin=str(self.locked_margin))
        return True

    def perp_target(self, symbol, marks):
        base = self.allocation_base(marks)
        spot, perp = self.budget_exposures(marks)
        budget = self.perp_budget(marks)
        requested = budget*self.config.perp_weights[symbol]
        target = max(ZERO, min(requested, budget-perp,
            budget-self.locked_margin*self.config.leverage,
            self.combined_budget(marks)-spot-perp,
            (self.free_cash-base*self.config.reserve)/(ONE/self.config.leverage+PERP_FEE+PERP_SLIP)))
        return base, requested, target

    def settle_funding(self, symbol, at, rate, mark):
        p = self.perps[symbol]
        paid = p.quantity*mark*rate
        if p.quantity:
            self.cash -= paid
            self.funding_paid += paid
            p.funding_paid += paid
            self.event(at, "funding", "recorded_settlement", symbol=symbol, market="perp", amount=str(paid))

    def close_perp(self, symbol, at, price, reason):
        p = self.perps[symbol]
        if not p.quantity:
            return
        notional = abs(p.quantity)*price
        fee, slip = notional*PERP_FEE, notional*PERP_SLIP
        gross = p.quantity*(price-p.entry_price)
        self.cash += gross-fee-slip
        self.fees += fee
        self.slippage += slip
        self.trades.append({"symbol": symbol, "market": "perp", "side": "long" if p.quantity > 0 else "short",
            "opened_at": p.entered_at.isoformat(), "closed_at": at.isoformat(), "entry_price": str(p.entry_price),
            "exit_price": str(price), "quantity": str(abs(p.quantity)), "exit_reason": reason,
            "gross_pnl": gross, "exchange_fee": p.entry_fee+fee, "slippage_cost": p.entry_slip+slip,
            "funding_paid": p.funding_paid, "net_pnl": gross-p.entry_fee-p.entry_slip-fee-slip-p.funding_paid})
        self.perps[symbol] = PerpPosition()
        self.event(at, "exit", reason, symbol=symbol, market="perp", price=str(price))

    def check_isolated_collateral(self, at):
        for s, p in self.perps.items():
            if p.quantity and p.margin+p.quantity*(self.perp_marks[s]-p.entry_price)-p.entry_fee-p.entry_slip-p.funding_paid <= 0:
                self.limitations.add("unsupported_liquidation")
                self.halted, self.halt_reason = True, "unsupported_liquidation"
                self.event(at, "halt", "unsupported_liquidation", symbol=s, market="perp")
