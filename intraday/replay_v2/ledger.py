"""Deterministic cash/position accounting; no exchange or persistent state."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from intraday.execution.allocation import DemoAllocation
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.replay_v2.contracts import ReplayConfig


ZERO = Decimal(0)
ONE = Decimal(1)
DAILY_LOSS = Decimal(".015")
MAX_DRAWDOWN = Decimal(".08")


class Ledger:
    def __init__(self, config: ReplayConfig):
        self.config = config
        self.cash = config.capital
        self.quantity = ZERO
        self.entry_price = None
        self.entered_at = None
        self.stop = None
        self.realized = self.costs = self.funding = ZERO
        self.peak = self.day_start = self.last_equity = config.capital
        self.day = config.start.date()
        self.halted = False
        self.halt_reason = None
        self.events, self.trades, self.curve = [], [], []
        self.max_drawdown = self.max_exposure = self.max_margin = ZERO
        self.entry_cost = self.entry_funding = ZERO
        self.last_mark = None
        weights = {config.symbol: ONE}
        self.allocation = DemoAllocation(capital=config.capital,
            **{"spot_weights" if config.market == "spot" else "perp_weights": weights})

    @property
    def funding_complete(self):
        history = self.config.profile.funding
        return self.config.market == "spot" or bool(history and
            history.coverage_start <= self.config.start and history.coverage_end >= self.config.end)

    def unrealized(self, mark):
        return self.quantity * (mark - self.entry_price) if self.entry_price is not None else ZERO

    def equity(self, mark):
        return self.cash + (self.quantity * mark if self.config.market == "spot" else self.unrealized(mark))

    def margin(self, mark):
        return abs(self.quantity) * mark / self.config.leverage if self.config.market == "perp" else ZERO

    def advance_day(self, at):
        if at.date() != self.day:
            self.day, self.day_start = at.date(), self.last_equity

    def state(self, at, mark):
        """Bridge to the existing pure risk coordinator, never the live runtime."""
        spot = self.config.market == "spot"
        return ParentPortfolioState(initial_equity=float(self.config.capital),
            realized_pnl=float(self.realized), fees=float(self.costs), funding=float(self.funding),
            spot_quantity=float(self.quantity) if spot else 0,
            spot_entry_price=float(self.entry_price) if spot and self.quantity else None,
            spot_entry_scope=self.config.scope if spot else "spot_daily",
            perp_quantity=float(self.quantity) if not spot else 0,
            perp_entry_price=float(self.entry_price) if not spot and self.quantity else None,
            mark_price=float(mark), spot_price=float(mark), perp_mark_price=float(mark),
            day_start_equity=float(self.day_start), high_water_mark=float(self.peak),
            entries_paused=self.halted, paper_active=True, updated_at=at)

    def _event(self, at, kind, reason, mark, **values):
        self.events.append({"at": at.isoformat(), "kind": kind, "reason": reason,
            "quantity": str(self.quantity), "cash": str(self.cash),
            "equity_known": str(self.equity(mark)), **values})

    def deny(self, at, mark, reason, **values):
        self._event(at, "entry_blocked", reason, mark, **values)
        return False

    def enter(self, at, price, notional, *, side, stop_distance):
        if self.halted:
            return self.deny(at, price, "portfolio_loss_limit")
        if self.quantity:
            return self.deny(at, price, "position_already_open")
        if self.config.market == "spot" and side != 1:
            return self.deny(at, price, "spot_long_only")
        equity = self.equity(price)
        cap = self.allocation.target_cap(self.config.symbol, self.config.market)
        fraction = Decimal(".30") if self.config.market == "spot" else Decimal(".20")
        if not ZERO < notional <= min(cap, equity * fraction):
            return self.deny(at, price, "allocation_limit")
        rules = self.config.profile.instrument
        quantity = notional / price
        stop = price * (ONE - stop_distance if side == 1 else ONE + stop_distance)
        if rules:
            quantity = rules.round_quantity(quantity)
            stop = (stop / rules.price_tick).to_integral_value(
                rounding=ROUND_CEILING if side == 1 else ROUND_FLOOR) * rules.price_tick
            try:
                rules.validate_quantity(quantity, price)
                rules.validate_price(stop)
            except ValueError:
                return self.deny(at, price, "instrument_filter")
        if quantity <= 0 or (side == 1 and stop >= price) or (side == -1 and stop <= price):
            return self.deny(at, price, "invalid_protection_or_quantity")
        notional = quantity * price
        cost = notional * self.config.profile.cost_bps(self.config.market) / 10000
        if self.config.market == "perp" and notional / self.config.leverage > equity * Decimal(".10"):
            return self.deny(at, price, "isolated_margin_limit")
        debit = notional + cost if self.config.market == "spot" else cost
        if debit > self.cash:
            return self.deny(at, price, "insufficient_cash")
        self.cash -= debit
        self.costs += cost
        self.quantity = quantity * side
        self.entry_price, self.stop, self.entered_at = price, stop, at
        self.entry_cost, self.entry_funding = cost, self.funding
        self._event(at, "entry", "signal", price, price=str(price), cost=str(cost), stop=str(stop), side=side)
        return True

    def close(self, at, price, reason):
        if not self.quantity:
            return
        quantity = self.quantity
        pnl = self.unrealized(price)
        cost = abs(quantity) * price * self.config.profile.cost_bps(self.config.market) / 10000
        self.cash += (quantity * price if self.config.market == "spot" else pnl) - cost
        self.realized += pnl
        self.costs += cost
        funding = self.funding - self.entry_funding
        self.trades.append({"entered_at": self.entered_at.isoformat(), "closed_at": at.isoformat(),
            "side": "long" if quantity > 0 else "short", "quantity": str(abs(quantity)),
            "entry_price": str(self.entry_price), "exit_price": str(price), "exit_reason": reason,
            "gross_pnl": str(pnl), "execution_cost": str(self.entry_cost + cost), "funding_known": str(funding),
            "net_known_pnl": str(pnl-self.entry_cost-cost-funding),
            "funding_complete": self.funding_complete})
        self.quantity = ZERO
        self.entry_price = self.entered_at = self.stop = None
        self._event(at, "close", reason, price, price=str(price), cost=str(cost), gross_pnl=str(pnl))

    def settle_funding(self, at, rate, mark):
        if self.config.market != "perp" or not self.quantity:
            return
        paid = self.quantity * mark * rate
        self.funding += paid
        self.cash -= paid
        self._event(at, "funding", "recorded_settlement", mark, amount=str(paid))

    def guard_reason(self, mark):
        equity = self.equity(mark)
        if equity <= self.peak * (ONE - MAX_DRAWDOWN):
            return "max_drawdown"
        if equity <= self.day_start * (ONE - DAILY_LOSS):
            return "daily_loss_limit"
        return None

    def halt(self, at, mark, reason):
        if not self.halted:
            self.halted, self.halt_reason = True, reason
            self._event(at, "halt", reason, mark)

    def observe(self, at, mark):
        self.advance_day(at)
        equity = self.equity(mark)
        self.peak = max(self.peak, equity)
        drawdown = (self.peak-equity)/self.peak if self.peak else ZERO
        self.max_drawdown = max(self.max_drawdown, drawdown)
        exposure = abs(self.quantity)*mark/equity if equity > 0 else ZERO
        margin = self.margin(mark)/equity if equity > 0 else ZERO
        self.max_exposure, self.max_margin = max(self.max_exposure, exposure), max(self.max_margin, margin)
        self.last_equity, self.last_mark = equity, mark
        self.curve.append({"at": at.isoformat(), "mark": str(mark), "cash": str(self.cash),
            "equity_known": str(equity), "unrealized_pnl": str(self.unrealized(mark)),
            "quantity": str(self.quantity), "notional": str(abs(self.quantity)*mark),
            "isolated_margin": str(self.margin(mark)), "drawdown_pct": str(drawdown*100)})
