"""Locked research-only Perp long/short accounts; runtime contracts remain unchanged."""

from decimal import Decimal as D
from typing import Literal

from pydantic import Field, field_validator, model_validator

from intraday.replay_v2.donchian_adx_setups import BasketConfig, H1_BASKET, basket_specification, matches
from intraday.replay_v2.donchian_filter_book import FilterBook
from intraday.replay_v2.contracts import positive_policy_number
from intraday.replay_v2.historical_capital import HistoricalBook
from intraday.replay_v2.historical_mixed import HistoricalConfig
from intraday.replay_v2.portfolio_book import ONE, ZERO
from intraday.replay_v2.metrics import fingerprint


def specification():
    return {**basket_specification(H1_BASKET), 'entry_cap': ZERO, 'perp_cap': ONE}


class PerpLeverageConfig(HistoricalConfig):
    universe: Literal['perp-leverage'] = 'perp-leverage'
    basket: tuple[Literal['ETH'], Literal['NEAR'], Literal['SOL']] = H1_BASKET
    weights: dict[str, D] = Field(default_factory=lambda: specification()['weights'])
    perp_weights: dict[str, D] = Field(default_factory=lambda: specification()['perp_weights'])
    adx_by_market: dict[str, dict[str, Literal[20]]] = Field(default_factory=lambda: specification()['adx_by_market'])
    filter_level: Literal[4] = 4
    entry_window: Literal[30] = 30
    exit_window: Literal[10] = 10
    adx_threshold: Literal[20] = 20
    entry_cap: Literal[0] = 0
    perp_cap: Literal[1] = 1
    reserve: Literal[0] = 0
    leverage: Literal[1, 5] = 5
    include_perp: Literal[True] = True
    trend_filter: Literal[False] = False
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_stop: Literal['atr14-3x'] = 'atr14-3x'
    perp_size: Literal['full'] = 'full'
    perp_daily_policy: Literal['disabled'] = 'disabled'
    perp_trade_exit: Literal['baseline'] = 'baseline'
    perp_trailing_interval: Literal['4h'] = '4h'
    hard_stop_pct: Literal[D('.10')] = D('.10')
    maintenance_margin_rate: Literal[D('.004')] = D('.004')
    cost_multiplier: Literal[1] = 1

    @field_validator('capital', 'daily_loss', 'max_drawdown', 'entry_cap')
    @classmethod
    def _numbers(cls, value, info):
        # Only this Perp-only configuration admits an empty Spot sleeve.
        return 0 if info.field_name == 'entry_cap' and value == 0 else positive_policy_number(value)

    @field_validator('hard_stop_pct', 'maintenance_margin_rate', mode='before')
    @classmethod
    def decimal_literal(cls, value):
        return D(str(value))

    @model_validator(mode='after')
    def locked_perp(self):
        if not matches(self, specification()):
            raise ValueError('Perp leverage allocation/filter/risk rules changed')
        return self


def perp_cases(start, end):
    return [('ETH-NEAR-SOL-H4', BasketConfig(start=start, end=end, basket=H1_BASKET,
                                           **basket_specification(H1_BASKET)))] + [
        (f'PERP{leverage}X', PerpLeverageConfig(start=start, end=end, leverage=leverage)) for leverage in (1, 5)]


class PerpLeverageBook(FilterBook):
    def __init__(self, config):
        super().__init__(config)
        self.max_locked_margin = ZERO
        self.liquidation_penalty = ZERO

    def observe(self, at, marks, *, stage='risk'):
        self.max_locked_margin = max(self.max_locked_margin, self.locked_margin)
        return super().observe(at, marks, stage=stage)

    def perp_target(self, symbol, marks):
        # The sleeve and weights cap MARGIN; execution, fees and slippage use NOTIONAL.
        budget, leverage = self.perp_budget(marks), D(self.config.leverage)
        requested = budget*self.perp_weight(symbol)*leverage
        cost = ONE/leverage+self.perp_fee+self.perp_slip
        target = max(ZERO, min(requested, (budget-self.locked_margin)/cost, self.free_cash/cost))
        return self.allocation_base(marks), requested, target

    def enter_perp(self, symbol, at, marks, price, side, stop_distance, *, approved_target=None):
        # Bypass FilterBook's legacy short-only guard, retaining HistoricalBook's realized accounting.
        entered = HistoricalBook.enter_perp(self, symbol, at, marks, price, side, stop_distance,
                                           approved_target=approved_target)
        if entered:
            p = self.perps[symbol]
            self.max_locked_margin = max(self.max_locked_margin, self.locked_margin)
            self.events[-1].update(margin=str(p.margin), leverage=self.config.leverage,
                                  liquidation_price=str(self.liquidation_price(p)))
        return entered

    def liquidation_price(self, position):
        side = ONE if position.quantity > 0 else -ONE
        return position.entry_price*(ONE-side*(ONE/D(self.config.leverage)-self.config.maintenance_margin_rate))

    def close_perp(self, symbol, at, price, reason):
        p, before = self.perps[symbol], self.cash
        if not p.quantity:
            return
        margin = p.margin
        super().close_perp(symbol, at, price, reason)
        if reason == 'liquidation':
            # Price PnL and exit costs are already booked; forfeit the remaining maintenance collateral.
            penalty = self.cash-before+margin
            self.cash -= penalty
            self.realized_capital['perp'] -= penalty
            self.liquidation_penalty += penalty
            self.trades[-1].update(liquidation_penalty=penalty, margin=str(margin))
            self.trades[-1]['net_pnl'] -= penalty
            self.event(at, 'liquidation', 'isolated_margin_forfeited', symbol=symbol, market='perp',
                       price=str(price), margin=str(margin), penalty=str(penalty))

    def check_isolated_collateral(self, at):
        if self.config.leverage == 1:
            return super().check_isolated_collateral(at)
        for s, p in self.perps.items():
            if p.quantity:
                threshold = self.liquidation_price(p)
                side = ONE if p.quantity > 0 else -ONE
                if side*(self.perp_marks[s]-threshold) <= 0:
                    self.close_perp(s, at, threshold, 'liquidation')

    def check_liquidations(self, at, bars, closed, *, opening):
        if self.config.leverage == 1:
            return
        for s, bar in sorted(bars.items()):
            p = self.perps[s]
            if not p.quantity:
                continue
            threshold = self.liquidation_price(p)
            observed = bar.open if opening else bar.low if p.quantity > 0 else bar.high
            side = ONE if p.quantity > 0 else -ONE
            if side*(observed-threshold) <= 0:
                self.close_perp(s, at, threshold, 'liquidation')
                closed.add(('perp', s))


def leverage_result(report, book):
    """Describe the opt-in model without changing legacy reports or result identities."""
    cfg = book.config
    report['config']['market'] = f'perp-long-short{cfg.leverage}x'
    report['evaluator_version'] += '-perp-leverage-v1'
    report['result_id'] = fingerprint(dict(version=report['evaluator_version'], config=report['config'],
                                          data=report['inputs']['dataset_checksum']))
    report['inputs']['config_checksum'] = fingerprint(report['config'])
    report['summary'].update(max_locked_margin_usdt=float(book.max_locked_margin),
        max_isolated_margin_pct=float(book.max_margin*100), max_perp_notional_pct=float(book.max_perp_exposure*100),
        hard_stop_exits=sum(t['exit_reason'].startswith('hard_stop') for t in book.trades),
        liquidations=sum(t['exit_reason'] == 'liquidation' for t in book.trades),
        liquidation_penalty_known=float(book.liquidation_penalty),
        hard_stop_liquidation_gap_pct=float((ONE/D(cfg.leverage)-cfg.maintenance_margin_rate-cfg.hard_stop_pct)*100),
        perp_sides={side: dict(closed_trades=sum(t['side'] == side for t in book.trades),
            net_pnl=float(sum((t['net_pnl'] for t in book.trades if t['side'] == side), ZERO)))
            for side in ('long', 'short')})
    report['methodology'].update(
        allocation=f'100% realized Perp capital; ETH/NEAR/SOL fixed thirds; margin=capital/3*min(1,.02/ATR%); '
                   f'notional={cfg.leverage}*margin; isolated long and short; no Spot; costs on notional; no unrealized sizing',
        liquidation=dict(maintenance_margin_rate=str(cfg.maintenance_margin_rate),
            enabled=cfg.leverage > 1, long='entry*(1-1/leverage+maintenance_margin_rate)',
            short='entry*(1+1/leverage-maintenance_margin_rate)',
            execution='mark M15 open or adverse high/low touch; fill at liquidation price; full margin forfeiture; '
                      'exit costs booked within forfeiture; continue with unchanged combined daily3%',
            ambiguous_intrabar_order='liquidation before trade-price stop when both M15 ranges touch'),
        hard_stop='closer of ATR14x3 ratchet and 10% entry price; never loosened')
    report['limitations'] = sorted(set(report['limitations'])-{'no_exact_liquidation_or_historical_demo_simulation'} |
        {'fixed_maintenance_margin_not_exchange_tiers', 'conservative_m15_liquidation_before_stop_order',
         'liquidation_price_not_tick_or_exchange_verified'})
    return report
