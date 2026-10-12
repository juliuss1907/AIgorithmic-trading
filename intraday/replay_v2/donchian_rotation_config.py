"""Setup-2 H4 with rotating positions, two separate sleeves and an open-book PnL gate; research only.

The frozen ETH-NEAR-SOL baskets give every coin a fixed third of each sleeve. This family replaces the
per-coin weights with a limit of open positions across both sleeves: a position may use the whole free
sleeve (ATR multiplier applied), a new signal can only enter by closing the weakest open position when
the book is full, new entries need the open book to be in profit and every position carries a fixed 5%
stop on top of the ATR trail. Every rule parameter not listed here is closed under Literals.

With three coins and one position per coin a three-position limit can never bind, so rotation is only
exercised by a four-coin basket. HYPE has no history in the frozen data; ZEC stands in as the fourth
coin (a volatile altcoin). The stand-in tests the mechanism, not HYPE.
"""

from decimal import Decimal as D
from typing import Literal

from pydantic import field_validator, model_validator

from intraday.replay_v2.donchian_adx_setups import (
    BasketConfig, BasketStressConfig, CANDIDATES, H1_BASKET, basket_name, basket_specification, matches, weights)
from intraday.replay_v2.historical_mixed import HistoricalConfig

SPLIT = D('.5')  # Two separate sleeves, 50% Spot and 50% Perp.
HARD_STOP = D('.05')
FOUR_COINS = ('ETH', 'NEAR', 'SOL', 'ZEC')


def equal_weights(basket):
    return dict(weights(**dict.fromkeys(basket, 1)))


class RotationConfig(HistoricalConfig):
    universe: Literal['rotation'] = 'rotation'
    basket: tuple[str, ...]
    max_positions: Literal[3, 4] = 3  # Open positions across both sleeves and all coins; one per coin.
    position_cap: Literal['none', 'slot'] = 'none'  # 'slot': one position at most 1/max_positions of its sleeve.
    rotation: Literal['off', 'adx'] = 'adx'  # How a full book makes room for a new signal.
    rotation_margin: Literal[5] = 5  # ADX points the candidate must beat the weakest open position by.
    pnl_gate: bool = True  # New entries only when nothing is open or the open book is in profit.
    hard_stop_pct: D | None = HARD_STOP
    filter_level: Literal[4] = 4
    entry_window: Literal[30] = 30
    exit_window: Literal[10] = 10
    adx_threshold: Literal[20] = 20
    entry_cap: D = SPLIT
    perp_cap: D = SPLIT
    reserve: Literal[0] = 0
    leverage: Literal[1] = 1
    trend_filter: Literal[False] = False
    include_perp: Literal[True] = True
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_stop: Literal['atr14-3x'] = 'atr14-3x'
    cost_multiplier: Literal[1, 2] = 1

    @field_validator('hard_stop_pct')
    @classmethod
    def known_stop(cls, value):
        if value is not None and value != HARD_STOP:
            raise ValueError('the hard stop is 5% of the entry price or absent')
        return value

    @model_validator(mode='after')
    def locked_rotation(self):
        if (not 3 <= len(self.basket) <= 4 or list(self.basket) != sorted(set(self.basket))
                or not set(self.basket) <= set(CANDIDATES) or self.max_positions > len(self.basket)):
            raise ValueError('rotation basket is three or four sorted distinct frozen coins, at least max_positions')
        shared = equal_weights(self.basket)
        if not matches(self, dict(weights=shared, perp_weights=shared, entry_cap=SPLIT, perp_cap=SPLIT)):
            raise ValueError('rotation study allocation or risk rules changed')
        return self


class RotationStressConfig(RotationConfig):
    cost_multiplier: Literal[2] = 2


class SplitBasketConfig(BasketConfig):
    """The frozen fixed-thirds basket with only the Spot/Perp split moved from 60/40 to 50/50."""
    entry_cap: D = SPLIT
    perp_cap: D = SPLIT

    def locked_overrides(self):
        return {'entry_cap': SPLIT, 'perp_cap': SPLIT}


class SplitBasketStressConfig(SplitBasketConfig):
    cost_multiplier: Literal[2] = 2


def rotation_cases(start, end):
    """Pre-registered. The frozen 60/40 control and the 50/50 fixed-thirds basket first, then the design
    and its one-mechanism ablations on ETH-NEAR-SOL, then the four-coin family where the position limit
    binds. All H4; ordered so one indicator preparation serves every case."""
    spec = basket_specification(H1_BASKET)
    shared = equal_weights(H1_BASKET)
    name = basket_name(H1_BASKET)
    cases = []

    def pair(label, normal, stressed, fields, basket=H1_BASKET, prefix=name, **change):
        for suffix, cls in (('', normal), ('-cost2x', stressed)):
            cases.append((f'{prefix}-{label}{suffix}', cls(start=start, end=end, basket=basket, **{**fields, **change})))

    def single(label, basket, prefix, **change):
        w = equal_weights(basket)
        cases.append((f'{prefix}-{label}', RotationConfig(start=start, end=end, basket=basket,
                                                          weights=w, perp_weights=dict(w), **change)))

    pair('H4', BasketConfig, BasketStressConfig, spec)
    pair('FIXED5050', SplitBasketConfig, SplitBasketStressConfig, spec, entry_cap=SPLIT, perp_cap=SPLIT)
    rot = dict(weights=shared, perp_weights=dict(shared))
    pair('ROT', RotationConfig, RotationStressConfig, rot)
    pair('ROT-SLOT', RotationConfig, RotationStressConfig, rot, position_cap='slot')
    for label, change in (('ROT-NOGATE', dict(pnl_gate=False)), ('ROT-NOSTOP', dict(hard_stop_pct=None)),
                          ('ROT-BARE', dict(pnl_gate=False, hard_stop_pct=None))):
        single(label, H1_BASKET, name, **change)
    four = basket_name(FOUR_COINS)
    w4 = equal_weights(FOUR_COINS)
    rot4 = dict(weights=w4, perp_weights=dict(w4))
    pair('ROT', RotationConfig, RotationStressConfig, rot4, basket=FOUR_COINS, prefix=four)
    pair('ROT-SLOT', RotationConfig, RotationStressConfig, rot4, basket=FOUR_COINS, prefix=four, position_cap='slot')
    for label, change in (('ROT-NOLIMIT', dict(max_positions=4)), ('ROT-NOROT', dict(rotation='off')),
                          ('ROT-BARE', dict(pnl_gate=False, hard_stop_pct=None))):
        single(label, FOUR_COINS, four, **change)
    return cases
