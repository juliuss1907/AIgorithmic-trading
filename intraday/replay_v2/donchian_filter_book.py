"""Opt-in 60/40 realized-only cash books and symmetric H4 ATR trailing."""

from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from intraday.replay_v2.historical_capital import HistoricalBook
from intraday.replay_v2.historical_mixed import HistoricalConfig
from intraday.replay_v2.mixed_book import PERP_FEE, PERP_SLIP
from intraday.replay_v2.portfolio_book import ZERO, ONE, FEE, SLIP


WEIGHTS = {'BTCUSDT':Decimal('.4'), 'ETHUSDT':Decimal('.3'), 'SOLUSDT':Decimal('.3')}


class FilterConfig(HistoricalConfig):
    study_weights: ClassVar[dict[str, Decimal]] = WEIGHTS
    entry_window: Literal[20, 30] = 20
    exit_window: Literal[8, 10] = 8
    filter_level: Literal[0, 1, 2, 3, 4] = 0
    weights: dict[str, Decimal] = Field(default_factory=lambda: dict(WEIGHTS))
    perp_weights: dict[str, Decimal] = Field(default_factory=lambda: dict(WEIGHTS))
    entry_cap: Decimal = Decimal('.6')
    perp_cap: Decimal = Decimal('.4')
    reserve: Literal[0] = 0
    leverage: Literal[1] = 1
    trend_filter: Literal[False] = False  # No legacy D1 filter.
    include_perp: Literal[True] = True
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_stop: Literal['atr14-3x'] = 'atr14-3x'
    daily_loss: Decimal = Decimal('.03')

    @model_validator(mode='after')
    def fixed_study(self):
        if (self.capital != 1000 or self.entry_cap != Decimal('.6') or self.perp_cap != Decimal('.4') or
                self.daily_loss != Decimal('.03') or self.weights != self.study_weights or self.perp_weights != self.study_weights or
                self.perp_daily_policy != 'disabled' or self.perp_trade_exit != 'baseline'):
            raise ValueError('filter study requires 60/40, BTC/ETH/SOL40/30/30 and parent-only daily3%')
        return self


class ATRTrail:
    def __init__(self, side, entry, atr, multiple=3, hard_pct=None):
        if side not in {-1, 1} or not entry.is_finite() or entry <= 0 or not atr.is_finite() or atr <= 0:
            raise ValueError('positive entry/ATR and valid trail side required')
        self.side, self.extreme, self.multiple = side, entry, multiple
        self.stop = entry-side*multiple*atr
        # A fixed percentage stop from the entry price. The tighter of it and the ATR stop wins, and the
        # ratchet below never loosens it, so it stays a hard cap on the initial loss.
        self.hard = None if hard_pct is None else entry*(ONE-side*hard_pct)
        if self.hard is not None:
            self.stop = max(self.stop, self.hard) if side > 0 else min(self.stop, self.hard)

    def update(self, high, low, atr):
        if not atr.is_finite() or atr <= 0:
            raise ValueError('positive finite trailing ATR required')
        self.extreme = max(self.extreme, high) if self.side > 0 else min(self.extreme, low)
        candidate = self.extreme-self.side*self.multiple*atr
        self.stop = max(self.stop, candidate) if self.side > 0 else min(self.stop, candidate)


class FilterBook(HistoricalBook):
    def __init__(self, config):
        super().__init__(config)
        multiplier = getattr(config, 'cost_multiplier', 1)
        self.spot_fee, self.spot_slip = FEE*multiplier, SLIP*multiplier
        self.perp_fee, self.perp_slip = PERP_FEE*multiplier, PERP_SLIP*multiplier
        self.trails = {}

    def observe(self, at, marks, *, stage='risk'):
        equity = super().observe(at,marks,stage=stage)
        self.curve[-1].update(day_start_equity=str(self.day_start),
            daily_return=str(equity/self.day_start-ONE))
        return equity

    def budget_exposures(self, marks):
        # Used principal cannot be reused when prices fall; gains do not consume cash.
        return (sum((p.quantity*p.entry_price for p in self.positions.values()), ZERO),
                sum((abs(p.quantity)*p.entry_price for p in self.perps.values()), ZERO))

    def combined_budget(self, marks):
        return self.spot_budget(marks)+self.perp_budget(marks)

    def perp_weight(self, symbol):
        return self.config.perp_weights[symbol]

    def enter_batch(self, at, marks, multipliers):
        budget = self.spot_budget(marks)
        used = self.budget_exposures(marks)[0]
        requests = sum((budget*self.weights[s]*m for s,m in multipliers.items()
                        if not self.positions[s].quantity), ZERO)
        scale = min(ONE, max(ZERO, budget-used)/(ONE+self.spot_fee+self.spot_slip)/requests) if requests else ZERO
        super().enter_batch(at, marks, {s:m*scale for s,m in multipliers.items()})

    def perp_target(self, symbol, marks):
        budget = self.perp_budget(marks)
        requested = budget*self.config.perp_weights[symbol]
        target = max(ZERO, min(requested, (budget-self.locked_margin)/(ONE+self.perp_fee+self.perp_slip),
                               self.free_cash/(ONE+self.perp_fee+self.perp_slip)))
        return self.allocation_base(marks), requested, target

    def enter_perp(self, symbol, at, marks, price, side, stop_distance, *, approved_target=None):
        if side != -1:
            raise ValueError('filter research Perp is short-only')
        return super().enter_perp(symbol, at, marks, price, side, stop_distance, approved_target=approved_target)

    def close(self, symbol, at, price, reason):
        super().close(symbol, at, price, reason)
        self.trails.pop(('spot', symbol), None)

    def close_perp(self, symbol, at, price, reason):
        super().close_perp(symbol, at, price, reason)
        self.trails.pop(('perp', symbol), None)


class SlotBook(FilterBook):
    """One realized-capital pool shared by Spot longs and Perp shorts, split into equal fixed slots.

    A slot is `1/slots` of the pool, scaled by the usual ATR multiplier. Which coins get a slot is the
    engine's decision; the book only sizes entries and keeps Spot and Perp inside the same pool."""

    def __init__(self, config):
        super().__init__(config)
        self.slot = ONE/config.slots
        self.weights = {s: self.slot for s in self.weights}

    def spot_budget(self, marks):
        return self.allocation_base(marks)

    def perp_budget(self, marks):
        return self.allocation_base(marks)

    def combined_budget(self, marks):
        return self.allocation_base(marks)

    def perp_weight(self, symbol):
        return self.slot

    def enter_batch(self, at, marks, multipliers):
        # FilterBook's pre-scaling looks at Spot principal only; the shared pool is checked once, below it.
        HistoricalBook.enter_batch(self, at, marks, multipliers)

    def perp_target(self, symbol, marks):
        pool = self.allocation_base(marks)
        spot, perp = self.budget_exposures(marks)
        requested = pool*self.slot
        cost = ONE+self.perp_fee+self.perp_slip
        target = max(ZERO, min(requested, (pool-spot-perp)/cost, self.free_cash/cost))
        return pool, requested, target


class RotationBook(FilterBook):
    """Two separate realized-capital sleeves (Spot and Perp) and no per-coin weights.

    A position may use up to `share` of its own sleeve's realized capital, before the ATR multiplier and
    bounded by that sleeve's free principal: 1 for the uncapped design, 1/max_positions for the slot
    comparison. Which coins get a position, and the three-position limit, are the engine's decision."""

    def __init__(self, config):
        super().__init__(config)
        self.share = ONE if config.position_cap == 'none' else ONE/config.max_positions
        self.weights = {s: self.share for s in self.weights}

    def perp_weight(self, symbol):
        return self.share

    def perp_target(self, symbol, marks):
        budget = self.perp_budget(marks)
        requested = budget*self.share
        cost = ONE+self.perp_fee+self.perp_slip
        target = max(ZERO, min(requested, (budget-self.locked_margin)/cost, self.free_cash/cost))
        return self.allocation_base(marks), requested, target
