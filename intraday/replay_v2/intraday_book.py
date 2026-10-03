"""Separate Perp-only risk observations from the H4 parent portfolio clock."""

from typing import Literal

from intraday.replay_v2.historical_mixed import HistoricalConfig
from intraday.replay_v2.historical_capital import HistoricalBook
from intraday.replay_v2.trade_trailing import TradeTrailingBook


class IntradayConfig(HistoricalConfig):
    capital_growth: Literal['realized'] = 'realized'
    perp_stop: Literal['atr14-3x'] = 'atr14-3x'
    perp_daily_policy: Literal['none'] = 'none'
    perp_trade_exit: Literal['net-trailing-3pp'] = 'net-trailing-3pp'
    perp_trailing_interval: Literal['1h', '15m'] = '1h'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_signal_interval: Literal['1h'] = '1h'
    perp_trend_profile: Literal['4h+8h-ema50'] = '4h+8h-ema50'
    perp_risk_interval: Literal['15m'] = '15m'


class IntradayBook(TradeTrailingBook):
    def __init__(self, config):
        super().__init__(config)
        self.perp_curve = []

    def record_perp(self, at, stage):
        self.perp_curve.append(dict(at=at.isoformat(), stage=stage, **self.daily_curve_fields()))

    def observe(self, at, marks, *, stage='risk'):
        equity = super().observe(at, marks, stage=stage)
        self.record_perp(at, 'parent_checkpoint')
        return equity

    def enforce_parent_risk(self, at, marks):
        # Bypass the combined daily evaluator, not the canonical parent ledger.
        # Dynamic observe still records Perp equity; parent has trigger priority.
        HistoricalBook.enforce_risk(self, at, marks)

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
        """Only H4, actual funding and fill-cost events call this combined check."""
        self.enforce_parent_risk(at, marks)
        self.enforce_perp_risk(at)
