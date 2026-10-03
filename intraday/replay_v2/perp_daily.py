"""Research-only Perp sleeve PnL and latched UTC daily policy; no broker access."""

from datetime import timedelta
from decimal import Decimal

from intraday.replay_v2.contracts import utc
from intraday.replay_v2.mixed_book import MixedBook
from intraday.replay_v2.portfolio_book import ZERO


LOSS = Decimal('.03')
ARM = Decimal('.03')
GIVEBACK = Decimal('.01')
TARGETS = {'target3': Decimal('.03'), 'target5': Decimal('.05')}


class PerpDailyState:
    def __init__(self, capital, at, policy):
        if not capital.is_finite() or capital <= 0 or policy not in {'none', 'target3', 'target5', 'trailing'}:
            raise ValueError('positive finite Perp capital and a supported daily policy are required')
        self.initial = self.cash = self.day_start = self.last_equity = self.peak_equity = capital
        self.policy, self.day, self.last_at = policy, utc(at).date(), utc(at)
        self.peak_return = self.max_drawdown = ZERO
        self.armed = self.locked = False
        self.reason = self.until = self.flattened_at = None

    @property
    def daily_return(self):
        return (self.last_equity-self.day_start)/self.day_start

    def advance_day(self, at):
        at = utc(at)
        if at < self.last_at:
            raise ValueError('Perp daily time cannot go backwards')
        if at.date() != self.day:
            self.day = at.date()
            if self.last_equity > 0:
                self.day_start = self.last_equity
            self.peak_return, self.armed = ZERO, False

    def observe(self, at, equity):
        self.advance_day(at)
        if not equity.is_finite():
            raise ValueError('Perp equity must be finite')
        self.last_at, self.last_equity = utc(at), equity
        self.peak_equity = max(self.peak_equity, equity)
        self.max_drawdown = max(self.max_drawdown, (self.peak_equity-equity)/self.peak_equity)
        self.peak_return = max(self.peak_return, self.daily_return)

    def evaluate(self, at):
        if self.locked:
            return None
        result = self.daily_return
        reason = ('perp_capital_exhausted' if self.last_equity <= 0 else
                  'perp_daily_loss' if result <= -LOSS else None)
        if self.policy == 'trailing' and result >= ARM:
            self.armed = True
        if reason is None:
            reason = ('perp_daily_profit' if self.policy in TARGETS and result >= TARGETS[self.policy] else
                      'perp_daily_trailing' if self.armed and result <= self.peak_return-GIVEBACK else None)
        if reason:
            self.locked, self.reason = True, reason
            self.until = utc(at).replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1)
            self.flattened_at = None
        return reason


class PerpDailyBook(MixedBook):
    def __init__(self, config):
        super().__init__(config)
        self.daily = PerpDailyState(config.capital*config.perp_cap, config.start, config.perp_daily_policy)

    def perp_equity(self):
        return self.daily.cash+sum((p.quantity*(self.perp_marks[s]-p.entry_price)
                                   for s, p in self.perps.items() if p.quantity), ZERO)

    def advance_day(self, at):
        super().advance_day(at)
        self.daily.advance_day(at)

    def enter_perp(self, symbol, at, marks, price, side, stop_distance, *, approved_target=None):
        if self.daily.locked:
            self.event(at, 'entry_blocked', self.daily.reason, symbol=symbol, market='perp')
            return False
        before = self.cash
        entered = super().enter_perp(symbol, at, marks, price, side, stop_distance, approved_target=approved_target)
        self.daily.cash += self.cash-before
        return entered

    def settle_funding(self, symbol, at, rate, mark):
        before = self.cash
        super().settle_funding(symbol, at, rate, mark)
        self.daily.cash += self.cash-before

    def close_perp(self, symbol, at, price, reason):
        before = self.cash
        super().close_perp(symbol, at, price, reason)
        self.daily.cash += self.cash-before
        self.note_perp_flat(at)

    def note_perp_flat(self, at):
        if self.daily.locked and self.daily.flattened_at is None and not any(p.quantity for p in self.perps.values()):
            self.daily.flattened_at = utc(at)
            self.event(at, 'perp_daily_flat', self.daily.reason, market='perp',
                       perp_equity=str(self.perp_equity()), day_start_equity=str(self.daily.day_start),
                       return_after_close=str((self.perp_equity()-self.daily.day_start)/self.daily.day_start))

    def daily_curve_fields(self):
        d = self.daily
        return dict(perp_equity=str(d.last_equity), perp_day_start_equity=str(d.day_start),
                    perp_daily_return=str(d.daily_return), perp_daily_peak_return=str(d.peak_return),
                    perp_daily_armed=d.armed, perp_daily_locked=d.locked, perp_daily_reason=d.reason)

    def observe(self, at, marks, *, stage='risk'):
        equity = super().observe(at, marks, stage=stage)
        self.daily.observe(at, self.perp_equity())
        self.curve[-1].update(self.daily_curve_fields())
        return equity

    def enforce_risk(self, at, marks):
        super().enforce_risk(at, marks)  # Parent insolvency/DD/daily loss have priority.
        armed_before = self.daily.armed
        reason = self.daily.evaluate(at) if not self.halted else None
        if self.daily.armed and not armed_before:
            self.event(at, 'perp_daily_armed', 'profit_reached_3pct', market='perp')
        if reason:
            if reason == 'perp_capital_exhausted':
                self.limitations.add(reason)
            self.event(at, 'perp_daily_halt', reason, market='perp',
                       **self.daily_curve_fields(), until=self.daily.until.isoformat())
            self.note_perp_flat(at)
        self.curve[-1].update(self.daily_curve_fields())

    def maybe_resume_perp(self, at):
        d = self.daily
        if (not d.locked or self.halted or d.reason == 'perp_capital_exhausted' or at < d.until
                or d.flattened_at is None or at <= d.flattened_at):
            return
        previous = d.reason
        d.locked, d.reason, d.until, d.flattened_at = False, None, None, None
        self.event(at, 'perp_daily_resume', 'next_utc_day_and_perp_flat', previous_reason=previous, market='perp')
