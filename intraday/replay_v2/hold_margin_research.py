"""Opt-in buy/hold and margin direction experiments, using frozen research only."""

from datetime import timedelta
from typing import Literal

from intraday.replay_v2.historical_mixed import close_touched_stops
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.portfolio_book import FEE, SLIP, ONE, ZERO, PortfolioBook, PortfolioConfig
from intraday.replay_v2.portfolio_research import portfolio_result, validate_inputs
from intraday.replay_v2.short_reserve_book import ShortReserveBook, ShortReserveConfig
from intraday.replay_v2.short_reserve_engine import DonchianSpotPolicy, NativeClock, MS, simulate_short_reserve


VERSION = 'historical-hold-margin-research-v1.0'


class AllocationConfig(ShortReserveConfig):
    leverage: Literal[1, 2] = 2
    perp_direction: Literal['short-only', 'long-short'] = 'short-only'
    spot_strategy: Literal['donchian', 'buy-and-hold'] = 'donchian'


class AllocationBook(ShortReserveBook):
    def allows_side(self, side):
        return side in {-1, 1} if self.config.perp_direction == 'long-short' else side == -1

    def maybe_resume(self, at, marks):
        if self.config.spot_strategy != 'buy-and-hold':
            return super().maybe_resume(at, marks)
        # The held Spot sleeve is never liquidated by an execution guard.
        # Parent daily locks still block/flatten Perps, and resume next UTC day.
        if (self.halt_reason != 'daily_loss_limit' or at < self.pause_until or
                any(p.quantity for p in self.perps.values()) or self.flat_at == at):
            return
        if self.equity(marks) <= self.day_start*(ONE-self.config.daily_loss):
            self.pause_until = at.replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1)
            return  # An overnight/new-day loss must not be erased on resumption.
        self.halted, self.halt_reason = False, None
        self.event(at, 'resume', 'next_utc_day_and_perp_flat_spot_held')


class HoldSpotPolicy(DonchianSpotPolicy):
    flatten_spot = False

    def touched(self, book, at, spot_close, perp_close):
        return close_touched_stops(book, at, {}, perp_close)

    def exits(self, book, at, spot_open, observations, closed):
        return None  # Window-end liquidation is owned by the shared engine.

    def entries(self, book, at, marks, observations, trends, closed):
        if at == book.config.start:
            book.enter_batch(at, marks, {s:ONE for s in book.weights})
        book.enforce_risk(at, marks)


def simulate_allocation(config, candles, daily, perp, funding, native):
    policy = HoldSpotPolicy() if config.spot_strategy == 'buy-and-hold' else DonchianSpotPolicy()
    report = simulate_short_reserve(config, candles, daily, perp, funding, native,
        book_class=AllocationBook, spot_policy=policy)
    report.update(evaluator_version=VERSION,
        result_id=fingerprint(dict(version=VERSION, config=report['config'], data=report['inputs']['dataset_checksum'])))
    report['methodology'].update(
        signals=f"Spot {config.spot_strategy}; Perp {config.perp_direction}, H4 Donchian30/8 with symmetric D1EMA50 direction/slope",
        allocation=f"Spot600 notional plus fill costs; Perp300 margin isolated{config.leverage}x; actual reserve100; own realized sizing; no unrealized sizing",
        perp_stop='10% price distance at entry, long below/short above; M15 gap/touch; no trailing')
    if config.spot_strategy == 'buy-and-hold':
        report['methodology'].update(spot_hold='buy once at start, fixed quantities, no stops/signal exit/rebalance; sell at end with costs',
            parent_daily_policy='parent3% daily loss blocks/flattens Perps only; held Spot never sold; resume requires next UTC day and Perp flat')
        report['limitations'] = sorted(set(report['limitations']) | {'buy_hold_spot_not_protected_by_parent_daily_stop'})
    return report


def simulate_buy_hold(start, end, candles, weights):
    cfg = PortfolioConfig(start=start, end=end, weights=weights, entry_cap=ONE)
    _, prepared, _ = validate_inputs(cfg, candles, {})
    clock = NativeClock(prepared, start, end)
    book = PortfolioBook(cfg)
    marks = {s:b.open for s,b in clock.opens[start].items()}
    # 100% total cash including entry costs. Round down two Decimal ulps so
    # canonical proportional fractional fills cannot overspend a tiny residue.
    gross = (cfg.capital/(ONE+FEE+SLIP)).next_minus()
    multiplier = (gross/cfg.capital).next_minus()
    book.enter_batch(start, marks, {s:multiplier for s in weights})
    for at in sorted(set(clock.opens) | set(clock.closes) | {end}):
        marks.update({s:b.open for s,b in clock.opens.get(at, {}).items()})
        marks.update({s:b.close for s,b in clock.closes.get(at, {}).items()})
        if at == end:
            for s,b in sorted(clock.closes[end-MS].items()):
                book.close(s, end, b.close, 'window_end')
        book.observe(at, marks)
        book.curve[-1]['perp_realized_capital'] = '0'
    report = portfolio_result(cfg, prepared, {}, book)
    report['config'] = {**cfg.model_dump(mode='json'), 'market':'spot-control',
        'symbol':'+'.join(s.removesuffix('USDT') for s in sorted(weights)),
        'spot_strategy':'buy-and-hold', 'perp_cap':'0', 'include_perp':False,
        'drawdown_policy':'observe-only'}
    report['summary']['contributions'] = {'spot:'+s:values for s,values in report['summary']['contributions'].items()}
    for trade in report['trades']:
        trade.update(market='spot', side='long', funding_paid=ZERO)
    for event in report['events']:
        event['market'] = 'spot'
        if event['kind'] == 'entry':
            event['reason'] = 'buy_and_hold_start'
    report['summary'].update(economic_check_only='not_applicable_benchmark', blockers=[])
    report.update(evaluator_version=VERSION,
        result_id=fingerprint(dict(version=VERSION, config=report['config'], data=report['inputs']['dataset_checksum'])))
    report['inputs']['config_checksum'] = fingerprint(report['config'])
    report['methodology'] = dict(allocation='100% initial cash incl entry costs; weights40/20/20/10/10',
        fills='buy start H4 open, sell final H4 close; unchanged quantities; no stops or rebalance',
        risk_sampling='H4 marked equity observe-only; no daily or DD execution halt',
        costs='Spot10bps fee and5bps slippage per fill; no interest on residual cash', official_gate_eligible=False)
    return report
