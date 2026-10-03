"""Opt-in historical research sizing; never changes recorded-Jev defaults."""

from intraday.replay_v2.mixed_book import MixedBook
from intraday.replay_v2.portfolio_book import ONE, ZERO


class HistoricalBook(MixedBook):
    def __init__(self, config):
        super().__init__(config)
        self.minimum_equity = config.capital
        self.first_peak_limit_at = self.first_initial_floor_at = None
        self.realized_capital = {'spot': config.capital*config.entry_cap,
                                 'perp': config.capital*config.perp_cap,
                                 'unallocated': config.capital*(ONE-config.entry_cap-config.perp_cap)}

    @property
    def terminal_risk_halted(self):
        return super().terminal_risk_halted or self.halt_reason == 'initial_capital_loss'

    def capital_stop_reason(self, equity):
        policy = self.config.drawdown_policy
        if policy == 'observe-only':
            return None
        if policy == 'initial-capital':
            return 'initial_capital_loss' if equity <= self.config.capital*(ONE-self.config.max_drawdown) else None
        return super().capital_stop_reason(equity)

    def observe(self, at, marks, *, stage='risk'):
        equity = super().observe(at, marks, stage=stage)
        self.minimum_equity = min(self.minimum_equity, equity)
        if equity <= self.peak*(ONE-self.config.max_drawdown) and self.first_peak_limit_at is None:
            self.first_peak_limit_at = at.isoformat()
        if equity <= self.config.capital*(ONE-self.config.max_drawdown) and self.first_initial_floor_at is None:
            self.first_initial_floor_at = at.isoformat()
        if self.config.capital_growth == 'realized':
            self.curve[-1].update({k+'_realized_capital': str(v) for k, v in self.realized_capital.items()})
        return equity

    def capital_guard_summary(self):
        policy = self.config.drawdown_policy
        return dict(policy=policy, initial_capital_floor=(
            float(self.config.capital*(ONE-self.config.max_drawdown)) if policy == 'initial-capital' else None),
            minimum_equity_known=float(self.minimum_equity),
            max_initial_capital_loss_pct=float(max(ZERO, ONE-self.minimum_equity/self.config.capital)*100),
            first_peak_drawdown_limit_at=self.first_peak_limit_at,
            first_initial_capital_floor_at=self.first_initial_floor_at)

    def allocation_base(self, marks):
        if self.config.capital_growth == 'realized':
            return max(ZERO, sum(self.realized_capital.values(), ZERO))
        if self.config.capital_growth == 'equity':
            # Revalue only new entry budgets. Existing positions are not rebalanced.
            return self.equity(marks)
        return super().allocation_base(marks)

    def spot_budget(self, marks):
        return (max(ZERO, self.realized_capital['spot']) if self.config.capital_growth == 'realized'
                else super().spot_budget(marks))

    def perp_budget(self, marks):
        return (max(ZERO, self.realized_capital['perp']) if self.config.capital_growth == 'realized'
                else super().perp_budget(marks))

    def budget_exposures(self, marks):
        spot, perp = super().budget_exposures(marks)
        if self.config.capital_growth == 'realized':
            # A falling mark must not make already-invested principal reusable.
            spot = max(spot, sum((p.quantity*p.entry_price for p in self.positions.values()), ZERO))
            perp = max(perp, sum((abs(p.quantity)*p.entry_price for p in self.perps.values()), ZERO))
        return spot, perp

    def enter_batch(self, at, marks, multipliers):
        before = self.fees+self.slippage
        budget, first_event = self.spot_budget(marks), len(self.events)
        super().enter_batch(at, marks, multipliers)
        self.realized_capital['spot'] -= self.fees+self.slippage-before
        if self.config.capital_growth == 'realized':
            for event in self.events[first_event:]:
                if event['kind'] == 'entry':
                    event.update(sizing_basis='separate_realized', market_sizing_capital=str(budget))

    def close(self, symbol, at, price, reason):
        p, before = self.positions[symbol], self.cash
        principal = p.quantity*p.entry_price
        super().close(symbol, at, price, reason)
        self.realized_capital['spot'] += self.cash-before-principal

    def enter_perp(self, symbol, at, marks, price, side, stop_distance, *, approved_target=None):
        before = self.cash
        budget = self.perp_budget(marks)
        entered = super().enter_perp(symbol, at, marks, price, side, stop_distance,
                                     approved_target=approved_target)
        self.realized_capital['perp'] += self.cash-before
        if entered and self.config.capital_growth == 'realized':
            self.events[-1].update(sizing_basis='separate_realized', market_sizing_capital=str(budget))
        return entered

    def close_perp(self, symbol, at, price, reason):
        before = self.cash
        super().close_perp(symbol, at, price, reason)
        self.realized_capital['perp'] += self.cash-before

    def settle_funding(self, symbol, at, rate, mark):
        before = self.cash
        super().settle_funding(symbol, at, rate, mark)
        self.realized_capital['perp'] += self.cash-before
