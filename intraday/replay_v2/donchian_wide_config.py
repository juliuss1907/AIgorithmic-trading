"""Setup-2 family on a wide coin list with a shared five-slot pool, research only.

The three-coin baskets lock their allocation in `BasketConfig`; this class replaces the fixed
60/40 split with one pool and a slot count, and keeps the rule parameters closed under Literals.
"""

from decimal import Decimal as D
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from intraday.replay_v2.historical_mixed import HistoricalConfig

POOL_SPLIT = D('.5')  # Seeds the two realized-capital keys only; SlotBook sizes from their sum.


def uniform_weights(symbols):
    """Equal weights that sum to exactly one (the last symbol absorbs the Decimal residual)."""
    ordered = sorted(symbols)
    share = D(1)/len(ordered)
    weights = {s: share for s in ordered}
    weights[ordered[-1]] = D(1)-sum(list(weights.values())[:-1], D(0))
    return weights


def identity_weights(cls, value):
    if not value or any(not re.fullmatch(r'[A-Z0-9]{2,20}USDT', s) or w <= 0 for s, w in value.items()) or sum(value.values()) != 1:
        raise ValueError('wide-slot weights are equal identity weights summing to one')
    return value


class WideSlotConfig(HistoricalConfig):
    universe: Literal['wide-slots'] = 'wide-slots'
    symbols: tuple[str, ...] = Field(min_length=6, max_length=60)
    slots: Literal[5] = 5
    ranker: Literal['adx_volume', 'random'] = 'adx_volume'
    seed: str = 'wide-slots-v1'
    entry_window: Literal[30] = 30
    exit_window: Literal[10, 40] = 10
    filter_level: Literal[4] = 4
    adx_threshold: Literal[20, 30] = 20
    bar_interval: Literal['4h', '1h'] = '4h'
    stop_atr_multiple: Literal[3, 6] = 3
    ema_fast: Literal[20, 50, 200] = 50
    ema_slow: Literal[50, 200, 800] = 200
    adx_period: Literal[14, 56] = 14
    warmup_hours: Literal[484, 1000] = 484
    regime_interval: Literal['4h'] | None = None
    cost_multiplier: Literal[1, 2] = 1
    entry_cap: D = POOL_SPLIT
    perp_cap: D = POOL_SPLIT
    reserve: Literal[0] = 0
    leverage: Literal[1] = 1
    trend_filter: Literal[False] = False
    include_perp: Literal[True] = True
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_stop: Literal['atr14-3x'] = 'atr14-3x'
    weights: dict[str, D] = Field(default_factory=dict)
    perp_weights: dict[str, D] = Field(default_factory=dict)

    valid_weights = field_validator('weights')(classmethod(identity_weights).__func__)
    _perp_weights = field_validator('perp_weights')(classmethod(identity_weights).__func__)

    @model_validator(mode='before')
    @classmethod
    def derive_weights(cls, data):
        if isinstance(data, dict) and 'symbols' in data:
            weights = uniform_weights(data['symbols'])
            data = {**data, 'weights': weights, 'perp_weights': dict(weights)}
        return data

    @model_validator(mode='after')
    def locked_study(self):
        if (len(set(self.symbols)) != len(self.symbols) or list(self.symbols) != sorted(self.symbols)
                or self.capital != 1000 or self.entry_cap != POOL_SPLIT or self.perp_cap != POOL_SPLIT
                or self.daily_loss != D('.03') or self.perp_daily_policy != 'disabled'
                or self.perp_trade_exit != 'baseline' or self.perp_size != 'full'
                or self.perp_trailing_interval != '4h'):
            raise ValueError('wide-slot study requires sorted unique symbols, 1000 USDT and the locked risk rules')
        if self.regime_interval and (self.bar_interval != '1h' or (self.ema_fast, self.ema_slow) != (50, 200)):
            raise ValueError('an H4 regime gate needs H1 bars with the default EMA pair')
        if self.bar_interval == '4h' and (self.warmup_hours != 484 or self.adx_period != 14):
            raise ValueError('H4 cases use the fixed warmup and ADX 14')
        if (self.ema_fast, self.ema_slow) not in ((50, 200), (20, 50), (200, 800)):
            raise ValueError('EMA pair must be 50/200, 20/50 or 200/800')
        slow = (self.ema_fast, self.ema_slow) == (200, 800)
        if slow != (self.adx_period == 56) or slow != (self.warmup_hours == 1000) or (
                slow and (self.bar_interval != '1h' or self.regime_interval)):
            raise ValueError('the slow H1 regime pairs EMA 200/800 with ADX 56, 1,000 h warmup and H1 bars')
        return self


class WideSlotStressConfig(WideSlotConfig):
    cost_multiplier: Literal[2] = 2


def wide_cases(start, end, symbols, only=None):
    """H4 reference and the H1 family, each at normal and doubled costs, plus a random-selection control.

    The registered list has 14 cases; `H1-SLOWREGIME` is added only on request through `only`. Ordered so
    cases that share an indicator preparation are adjacent."""
    symbols = tuple(sorted(symbols))
    rows = [('H4', {}), ('H4-random', dict(ranker='random')),
            ('H1', dict(bar_interval='1h')),
            ('H1-GEOM', dict(bar_interval='1h', stop_atr_multiple=6, exit_window=40)),
            ('H1-ADX30', dict(bar_interval='1h', adx_threshold=30)),
            ('H1-EMA50-20', dict(bar_interval='1h', ema_fast=20, ema_slow=50)),
            ('H1-REGIME', dict(bar_interval='1h', regime_interval='4h')),
            ('H1-SLOWREGIME', dict(bar_interval='1h', ema_fast=200, ema_slow=800, adx_period=56, warmup_hours=1000))]
    cases = []
    for label, change in rows:
        if only is None and label == 'H1-SLOWREGIME':
            continue
        for suffix, cls in (('', WideSlotConfig), ('-cost2x', WideSlotStressConfig)):
            name = f'WIDE-{label}{suffix}'
            if only is None or name in only:
                cases.append((name, cls(start=start, end=end, symbols=symbols, **change)))
    if only is not None and {n for n, _ in cases} != set(only):
        raise ValueError('unknown wide case requested')
    return cases
