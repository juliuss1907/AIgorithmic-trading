"""Offline USD cash-account research; no crypto runtime or broker bridge."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

import exchange_calendars as xcals
from pydantic import Field, field_validator, model_validator

from intraday.contracts import SpotRuleParameters
from intraday.replay_v2.contracts import FrozenModel, utc, positive_policy_number
from intraday.replay_v2.indicators import ema50_trend, trend_side
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.portfolio_book import ONE, ZERO, Position, PortfolioBook
from intraday.spot_signal import evaluate_donchian
from lab.data import adjust, validate

SYMBOLS = ('GOOGL', 'META', 'MSFT', 'NVDA')
VERSION = 'equity-daily-cash-research-v1'


class EquityConfig(FrozenModel):
    start: datetime = datetime(2024, 10, 2, tzinfo=timezone.utc)
    end: datetime = datetime(2026, 10, 2, tzinfo=timezone.utc)
    data_start: date = date(2024, 7, 1)
    capital: Decimal = Field(default=Decimal(1000), gt=0, le=10000)
    weights: dict[str, Decimal] = Field(default_factory=lambda: {s: Decimal('.25') for s in SYMBOLS})
    entry_cap: Decimal = Field(default=ONE, gt=0, le=1)
    daily_loss: Decimal = Field(default=Decimal('.03'), gt=0, lt=1)
    max_drawdown: Decimal = Field(default=Decimal('.10'), gt=0, lt=1)
    commission: Decimal = Field(default=Decimal('.0005'), ge=0, lt='.01')
    slippage: Decimal = Field(default=Decimal('.0005'), ge=0, lt='.01')
    stop: Literal['fixed-5pct', 'atr14-3x'] = 'fixed-5pct'
    strategy: Literal['donchian', 'buy-and-hold'] = 'donchian'

    _times = field_validator('start', 'end')(utc)
    _numbers = field_validator('capital', 'entry_cap', 'daily_loss', 'max_drawdown')(positive_policy_number)

    @field_validator('weights')
    @classmethod
    def allocations(cls, value):
        if set(value) != set(SYMBOLS) or any(not v.is_finite() or not ZERO < v <= ONE for v in value.values()) or sum(value.values()) != ONE:
            raise ValueError('four configured equity weights must be positive and sum to one')
        return dict(sorted(value.items()))

    @model_validator(mode='after')
    def dates(self):
        if self.start >= self.end or self.data_start >= self.start.date() or any(
            t.hour or t.minute or t.second or t.microsecond for t in (self.start, self.end)):
            raise ValueError('ordered UTC date boundaries and prior warmup required')
        return self


class EquityBook(PortfolioBook):
    """Reuse cash valuation/observations, with equity-specific costs and exits."""

    def __init__(self, config):
        super().__init__(config)
        self.stops = {}
        self.halt_session = None

    def enter(self, at, marks, requests, signals):
        if self.halted or not requests:
            return
        cfg = self.config
        base = min(cfg.capital, self.equity(marks))
        exposure = sum(self.positions[s].quantity*marks[s] for s in self.weights)
        budgets = {s: base*cfg.entry_cap*self.weights[s]*m for s, m in sorted(requests.items())}
        room = max(ZERO, min(base*cfg.entry_cap-exposure, self.cash/(ONE+cfg.commission+cfg.slippage)))
        scale = min(ONE, room/sum(budgets.values())) if sum(budgets.values()) else ZERO
        for s, budget in budgets.items():
            if budget*scale <= 0:
                continue
            quantity = budget*scale/marks[s]
            if quantity*marks[s] > budget*scale:
                quantity = quantity.next_minus()
            notional = quantity*marks[s]
            fee, slip = notional*cfg.commission, notional*cfg.slippage
            self.cash -= notional+fee+slip
            self.fees += fee
            self.slippage += slip
            self.positions[s] = Position(quantity, marks[s], fee, slip, at)
            fraction, available = signals[s]
            self.stops[s] = marks[s]*(ONE-fraction)
            self.event(at, 'entry', cfg.strategy, symbol=s, price=str(marks[s]), quantity=str(quantity),
                       notional=str(notional), requested_notional=str(budget), stop_price=str(self.stops[s]),
                       signal_available_at=available.isoformat())
        if self.cash < 0:
            raise ValueError('equity cash conservation failed')

    @property
    def fee_rate(self):
        return self.config.commission

    @property
    def slip_rate(self):
        return self.config.slippage

    def close(self, symbol, at, price, reason):
        if not self.positions[symbol].quantity:
            return
        super().close(symbol, at, price, reason)
        # Keep the published equity journal fields: 'commission', no cash on exit events.
        self.trades[-1]['commission'] = self.trades[-1].pop('exchange_fee')
        self.events[-1].pop('cash')

    def enforce_risk(self, at, marks):
        equity = self.observe(at, marks)
        if self.config.strategy == 'buy-and-hold' or self.halt_reason == 'max_drawdown':
            return
        reason = ('max_drawdown' if equity <= self.peak*(ONE-self.config.max_drawdown) else
                  'daily_loss_limit' if equity <= self.day_start*(ONE-self.config.daily_loss) else None)
        if reason and reason != self.halt_reason:
            self.halted, self.halt_reason, self.halt_session = True, reason, at.date()
            self.event(at, 'halt', reason)


def prepare(config, frames):
    if set(frames) != set(config.weights):
        raise ValueError('all four equity histories required')
    end = str(config.end.date()-timedelta(days=1))
    schedule = xcals.get_calendar('XNAS', start=str(config.data_start), end=end).schedule
    prepared, signals = {}, {}
    rule = SpotRuleParameters(entry_window=30, exit_window=8, atr_period=14)
    for s, raw in sorted(frames.items()):
        validate(raw, str(config.data_start), end)
        frame = adjust(raw)
        validate(frame, str(config.data_start), end)
        if sum(frame.index.date < config.start.date()) < 51:
            raise ValueError('51 prior sessions needed for EMA50 slope and channel warmup')
        rows = [[i, *values] for i, values in enumerate(frame[['open', 'high', 'low', 'close', 'volume']].values.tolist())]
        closes = [Decimal(str(row[4])) for row in rows]
        observations = []
        for i, (close, (ema, prior)) in enumerate(zip(closes, ema50_trend(closes))):
            obs = evaluate_donchian(rows[max(0, i-30):i+1], rule) if i >= 30 else None
            observations.append((obs, trend_side(close, ema, prior) == 1))
        prepared[s], signals[s] = frame, observations
    return schedule, prepared, signals


def simulate(config, frames):
    schedule, prices, signals = prepare(config, frames)
    book = EquityBook(config)
    used = [d for d in schedule.index if config.start.date() <= d.date() < config.end.date()]
    if not used:
        raise ValueError('evaluation window contains no equity sessions')
    for session in used:
        at, closed_at = (schedule.loc[session, k].to_pydatetime() for k in ('open', 'close'))
        if at.date() != book.day:
            book.day, book.day_start = at.date(), book.last_equity
        marks = {s: Decimal(str(f.loc[session, 'open'])) for s, f in prices.items()}
        previous = {s: signals[s][f.index.get_loc(session)-1] for s, f in prices.items()}
        exited = set()
        book.enforce_risk(at, marks)
        for s in config.weights:
            obs, _ = previous[s]
            reason = (book.halt_reason if book.halted else 'stop_gap'
                if config.strategy == 'donchian' and book.positions[s].quantity and marks[s] <= book.stops[s]
                else 'donchian_exit' if config.strategy == 'donchian' and obs.exit else None)
            if book.positions[s].quantity and reason:
                book.close(s, at, marks[s], reason)
                exited.add(s)
        book.enforce_risk(at, marks)
        if book.halt_reason == 'daily_loss_limit' and at.date() > book.halt_session and not any(p.quantity for p in book.positions.values()):
            book.halted, book.halt_reason, book.day_start = False, None, book.equity(marks)
            book.event(at, 'resume', 'next_session_and_flat')
        requests, stops = {}, {}
        for s in config.weights:
            obs, trend = previous[s]
            benchmark = config.strategy == 'buy-and-hold' and session == used[0]
            if book.positions[s].quantity or s in exited or book.halted or not (benchmark or obs.entry and trend):
                continue
            distance = Decimal('.05') if config.stop == 'fixed-5pct' else 3*Decimal(str(obs.atr))/marks[s]
            if not ZERO < distance < ONE:
                book.event(at, 'entry_blocked', 'invalid_stop_distance', symbol=s)
                continue
            requests[s] = ONE if benchmark else Decimal(str(obs.size_multiplier))
            prior_session = prices[s].index[prices[s].index.get_loc(session)-1]
            stops[s] = distance, schedule.loc[prior_session, 'close'].to_pydatetime()
        book.enter(at, marks, requests, stops)
        book.enforce_risk(at, marks)
        marks = {s: Decimal(str(f.loc[session, 'close'])) for s, f in prices.items()}
        if config.strategy == 'donchian':
            for s, f in prices.items():
                if book.positions[s].quantity and Decimal(str(f.loc[session, 'low'])) <= book.stops[s]:
                    book.close(s, closed_at, book.stops[s], 'stop_touch_detected_at_close')
        book.enforce_risk(closed_at, marks)
        if session == used[-1]:
            for s in config.weights:
                book.close(s, closed_at, marks[s], 'window_end')
            book.enforce_risk(closed_at, marks)
    return result(config, frames, book, len(used))


def result(config, frames, book, count):
    net = book.cash-config.capital
    if abs(sum((t['net_pnl'] for t in book.trades), ZERO)-net) > Decimal('1e-18'):
        raise ValueError('USD trade PnL does not reconcile to cash')
    data_hash = fingerprint({s: {'dates': [str(d.date()) for d in f.index],
        'columns': f.columns.tolist(), 'rows': f.values.tolist()} for s, f in sorted(frames.items())})
    cfg = config.model_dump(mode='json')
    terminal = next((e['at'] for e in book.events if e['kind'] == 'halt' and e['reason'] == 'max_drawdown'), None)
    blockers = ([] if net > 0 else ['nonpositive_net_return'])+(
        ['drawdown_at_or_above_limit'] if book.max_drawdown >= config.max_drawdown else [])+(
        ['minimum_6_closed_trades'] if len(book.trades) < 6 else [])
    return {'evaluator_version': VERSION, 'currency': 'USD', 'config': cfg, 'dataset_checksum': data_hash,
        'result_id': fingerprint({'version': VERSION, 'config': cfg, 'data': data_hash}),
        'research_only': True, 'activation_allowed': False, 'official_gate_eligible': False,
        'summary': {'final_equity': float(book.cash), 'net_pnl': float(net),
            'net_return_pct': float(net/config.capital*100), 'max_drawdown_pct': float(book.max_drawdown*100),
            'closed_trades': len(book.trades), 'sessions': count, 'commission': float(book.fees),
            'slippage_cost': float(book.slippage), 'stop_exits': sum(t['exit_reason'].startswith('stop_') for t in book.trades),
            'daily_pause_count': sum(e['kind'] == 'halt' and e['reason'] == 'daily_loss_limit' for e in book.events),
            'terminal_halt_at': terminal, 'max_exposure_pct': float(book.max_exposure*100),
            'per_symbol': {s: {'closed_trades': sum(t['symbol'] == s for t in book.trades),
                'net_pnl': float(sum((t['net_pnl'] for t in book.trades if t['symbol'] == s), ZERO))} for s in config.weights},
            'economic_check_only': 'benchmark_not_gated' if config.strategy == 'buy-and-hold' else 'reject' if blockers else 'pass',
            'blockers': blockers if config.strategy == 'donchian' else []},
        'methodology': {'calendar': 'XNAS cash sessions, DST/holidays/early closes from exchange_calendars',
            'signals': 'prior closed session Donchian30/8, SMA-seeded EMA50 slope; ATR14 simple mean TR',
            'sizing': 'min(initial capital,equity)*25%*min(1,2%/ATR%); cap100%, no periodic rebalancing',
            'fills': 'next regular-session open; stop gap at open, low touch at stop detected at close',
            'risk': 'prior close to session open/close; daily pause resumes only next session AND flat; DD terminal',
            'benchmark': 'equal-weight buy-and-hold, same capital/cap/costs; no ATR sizing, stops or risk halts',
            'costs': 'assumed 5bps commission + 5bps cash-charged slippage each fill; not a verified broker fee',
            'prices': 'synthetic total-return adjusted OHLC; dividends implicit, never credited twice'},
        'limitations': ['research_only_not_activation_gate', '1d_not_comparable_to_crypto_4h',
            'synthetic_adjusted_units_not_executable_historical_quotes', 'fractional_shares_assumed',
            'assumed_costs_no_tax_fx_or_broker_regulatory_fees', 'intraday_dd_and_stop_timing_unknown',
            'no_LLM_no_funding_no_short_no_leverage', 'selected_surviving_tickers_ex_post_not_independent_holdout',
            'terminal_halt_leaves_remaining_window_idle'],
        'equity_curve': book.curve, 'trades': book.trades, 'events': book.events}
