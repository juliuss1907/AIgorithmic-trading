"""Research-only Perp sleeve PnL and latched UTC daily policy; no broker access."""

from datetime import timedelta
from decimal import Decimal

from intraday.replay_v2.contracts import utc
from intraday.replay_v2.historical_capital import HistoricalBook
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
        if at < self.last_at or at.date() < self.day:
            raise ValueError('Perp daily time cannot go backwards')
        self.last_at = at
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


class PerpDailyBook(HistoricalBook):
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


def daily_summary(book, valid=True):
    """Descriptive statistics, not an activation or a newly optimized gate."""
    d = book.daily
    trades = [t for t in book.trades if t['market'] == 'perp']
    net = sum((t['net_pnl'] for t in trades), ZERO)
    if abs(d.cash-d.initial-net) > Decimal('1e-18'):
        raise ValueError('Perp sleeve does not reconcile to trade journal')
    days = {}
    giveback = ZERO
    for row in book.curve:
        day = row['at'][:10]
        daily_return = Decimal(row['perp_daily_return'])
        days[day] = daily_return
        giveback = max(giveback, Decimal(row['perp_daily_peak_return'])-daily_return)
    # The end-exclusive boundary is not an additional traded day.
    if book.config.end.time().isoformat() == '00:00:00':
        days.pop(book.config.end.date().isoformat(), None)
    halts = [e for e in book.events if e['kind'] == 'perp_daily_halt']
    profit = sum(e['reason'] == 'perp_daily_profit' for e in halts)
    trailing = sum(e['reason'] == 'perp_daily_trailing' for e in halts)
    evidence = ('insufficient_perp_trades' if len(trades) < 6 else
                'policy_not_triggered' if d.policy != 'none' and profit+trailing == 0 else
                'descriptive_only_not_holdout')
    return {'policy': d.policy, 'initial_equity': float(d.initial), 'final_equity': float(d.cash),
            'net_pnl': float(net) if valid else None, 'pnl_after_known_costs': float(net),
            'net_return_pct': float(net/d.initial*100) if valid else None,
            'max_drawdown_known_pct': float(d.max_drawdown*100), 'closed_trades': len(trades),
            'exchange_fee': float(sum((t['exchange_fee'] for t in trades), ZERO)),
            'slippage_cost': float(sum((t['slippage_cost'] for t in trades), ZERO)),
            'funding_paid': float(sum((t['funding_paid'] for t in trades), ZERO)),
            'loss_halts': sum(e['reason'] == 'perp_daily_loss' for e in halts),
            'profit_halts': profit, 'trailing_halts': trailing,
            'armed_days': len({e['at'][:10] for e in book.events if e['kind'] == 'perp_daily_armed'}),
            'resumes': sum(e['kind'] == 'perp_daily_resume' for e in book.events),
            'blocked_entries': sum(e['kind'] == 'entry_blocked' and e['reason'].startswith('perp_daily_')
                                   for e in book.events),
            'observed_days': len(days), 'worst_observed_day_pct': float(min(days.values(), default=ZERO)*100),
            'max_observed_giveback_pp': float(giveback*100), 'evidence': evidence}
