"""Opt-in short margin budget and flat-only reserve flows, never broker execution."""

from decimal import Decimal
from typing import Literal

from pydantic import model_validator

from intraday.replay_v2.historical_mixed import HistoricalConfig
from intraday.replay_v2.historical_capital import HistoricalBook
from intraday.replay_v2.mixed_book import PERP_FEE, PERP_SLIP
from intraday.replay_v2.perp_daily import PerpDailyBook, PerpDailyState
from intraday.replay_v2.portfolio_book import ONE, ZERO


class ShortReserveConfig(HistoricalConfig):
    include_perp: Literal[True] = True
    leverage: Literal[2] = 2
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_daily_policy: Literal['none'] = 'none'
    perp_trade_exit: Literal['baseline'] = 'baseline'
    perp_trailing_interval: Literal['4h'] = '4h'
    perp_stop: Literal['fixed-10pct'] = 'fixed-10pct'
    perp_size: Literal['full'] = 'full'
    allocation_basis: Literal['perp-margin'] = 'perp-margin'
    perp_direction: Literal['short-only'] = 'short-only'
    reserve_policy: Literal['off', 'restore-and-repay'] = 'off'
    perp_risk_interval: Literal['15m'] = '15m'

    @model_validator(mode='after')
    def fixed_allocation(self):
        if (self.entry_cap != Decimal('.60') or self.perp_cap != Decimal('.30') or
                self.reserve != Decimal('.10') or self.perp_weights !=
                {'BTCUSDT':Decimal('.50'), 'ETHUSDT':Decimal('.50')}):
            raise ValueError('short-reserve requires 60/30/10 allocation and BTC/ETH 50/50 Perp')
        return self


class FlowDailyState(PerpDailyState):
    """Actual day-start equity, flow-neutral returns and cumulative trading DD."""
    def __init__(self, capital, at):
        super().__init__(capital, at, 'none')
        self.capital_flows = self.day_flows = ZERO

    @property
    def daily_return(self):
        return (self.last_equity-self.day_start-self.day_flows)/self.day_start

    def advance_day(self, at):
        old_day = self.day
        super().advance_day(at)
        if self.day != old_day:
            self.day_flows = ZERO

    def observe(self, at, equity):
        self.advance_day(at)
        if not equity.is_finite():
            raise ValueError('Perp equity must be finite')
        self.last_equity = equity
        performance = equity-self.capital_flows
        self.peak_equity = max(self.peak_equity, performance)
        self.max_drawdown = max(self.max_drawdown, ONE-performance/self.peak_equity)
        self.peak_return = max(self.peak_return, self.daily_return)

    def transfer(self, amount):
        self.cash += amount
        self.last_equity += amount
        self.capital_flows += amount
        self.day_flows += amount


class ShortReserveBook(PerpDailyBook):
    def __init__(self, config):
        super().__init__(config)
        self.daily = FlowDailyState(config.capital*config.perp_cap, config.start)
        self.reserve_initial = config.capital*config.reserve
        self.drawn = self.repaid = ZERO
        self.perp_curve, self.transfers = [], []
        self.flat_at = None

    @property
    def reserve_balance(self):
        return self.realized_capital['unallocated']

    def reserve_floor(self, marks):
        return self.reserve_balance

    def combined_budget(self, marks):
        # Spot own principal cap only. Perp is limited by margin, not notional.
        return self.spot_budget(marks)+self.budget_exposures(marks)[1]

    def perp_target(self, symbol, marks):
        budget = self.perp_budget(marks)
        requested = budget*self.config.perp_weights[symbol]*self.config.leverage
        denominator = ONE/self.config.leverage+PERP_FEE+PERP_SLIP
        target = max(ZERO, min(requested, (budget-self.locked_margin)/denominator,
            (self.free_cash-self.reserve_balance)/denominator))
        return self.allocation_base(marks), requested, target

    def allows_side(self, side):
        return side == -1

    def enter_perp(self, symbol, at, marks, price, side, stop_distance, *, approved_target=None):
        if not self.allows_side(side) or stop_distance != Decimal('.10'):
            raise ValueError('short-only requires a fixed 10% price stop')
        if self.flat_at == at:
            self.event(at, 'entry_blocked', 'perp_flat_batch_cooldown', symbol=symbol, market='perp')
            return False
        return super().enter_perp(symbol, at, marks, price, side, stop_distance,
                                 approved_target=approved_target)

    def close_perp(self, symbol, at, price, reason):
        p = self.perps[symbol]
        if not p.quantity:
            return
        entry_notional = abs(p.quantity)*p.entry_price
        held = (at-p.entered_at).total_seconds()/3600
        super().close_perp(symbol, at, price, reason)
        self.trades[-1].update(entry_notional=str(entry_notional), holding_hours=held)
        if not any(p.quantity for p in self.perps.values()):
            self.flat_at = at

    def rebalance_reserve(self, at, marks):
        if (self.config.reserve_policy == 'off' or at >= self.config.end or
                any(p.quantity for p in self.perps.values())):
            return
        p, r, initial = self.realized_capital['perp'], self.reserve_balance, self.daily.initial
        amount = min(initial-p, r) if p < initial else -min(p-initial, self.reserve_initial-r)
        if not amount:
            return
        self.realized_capital['perp'] += amount
        self.realized_capital['unallocated'] -= amount
        self.daily.transfer(amount)
        self.drawn += max(ZERO, amount)
        self.repaid += max(ZERO, -amount)
        values = dict(amount=str(amount), perp_capital=str(self.realized_capital['perp']),
            reserve_balance=str(self.reserve_balance), net_debt=str(self.reserve_initial-self.reserve_balance))
        self.event(at, 'reserve_transfer', 'draw' if amount > 0 else 'repay', **values)
        self.transfers.append(dict(at=at.isoformat(), **values))
        self.observe(at, marks, stage='reserve_transfer')

    def daily_curve_fields(self):
        return {**super().daily_curve_fields(), 'perp_capital_flows':str(self.daily.capital_flows),
            'perp_performance_equity':str(self.daily.last_equity-self.daily.capital_flows),
            'reserve_balance':str(self.reserve_balance)}

    def record_perp(self, at, stage):
        self.perp_curve.append(dict(at=at.isoformat(), stage=stage, **self.daily_curve_fields()))

    def observe(self, at, marks, *, stage='risk'):
        equity = super().observe(at, marks, stage=stage)
        self.curve[-1]['perp_capital_flows'] = str(self.daily.capital_flows)
        self.record_perp(at, 'parent_checkpoint')
        return equity

    def enforce_perp_risk(self, at):
        self.daily.observe(at, self.perp_equity())
        reason = self.daily.evaluate(at) if not self.halted else None
        if reason:
            if reason == 'perp_capital_exhausted':
                self.limitations.add(reason)
            self.event(at, 'perp_daily_halt', reason, market='perp',
                **self.daily_curve_fields(), until=self.daily.until.isoformat())
            self.note_perp_flat(at)
        self.record_perp(at, 'perp_risk')

    def enforce_risk(self, at, marks):
        HistoricalBook.enforce_risk(self, at, marks)
        self.enforce_perp_risk(at)
