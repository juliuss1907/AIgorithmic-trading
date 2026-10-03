"""Opt-in historical research sizing; never changes recorded-Jev defaults."""

from intraday.replay_v2.mixed_book import MixedBook
from intraday.replay_v2.portfolio_book import ONE, ZERO


class HistoricalBook(MixedBook):
    def __init__(self, config):
        super().__init__(config)
        self.minimum_equity = config.capital
        self.first_peak_limit_at = self.first_initial_floor_at = None

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
        if self.config.capital_growth == 'equity':
            # Revalue only new entry budgets. Existing positions are not rebalanced.
            return self.equity(marks)
        return super().allocation_base(marks)
