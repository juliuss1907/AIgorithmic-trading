"""The five user-requested allocation/ADX setups, locked before replay."""

from decimal import Decimal as D
from itertools import combinations
from typing import Literal

from pydantic import Field, model_validator

from intraday.replay_v2.historical_mixed import HistoricalConfig


def weights(**ratios):
    total = sum(ratios.values())
    result = {s+'USDT': D(r)/total for s, r in sorted(ratios.items())}
    # Exact sum at Decimal precision, including equal thirds.
    last = next(reversed(result))
    result[last] = D(1)-sum(list(result.values())[:-1], D(0))
    return result


MAJORS = weights(BTC=4, ETH=3, SOL=3)
ALT = weights(SOL=4, ZEC=3, NEAR=3)
PAIR = weights(BTC=1, ETH=1)
THIRDS = weights(SOL=1, ZEC=1, NEAR=1)
MIXED = weights(BTC=260, ETH=195, SOL=195, NEAR=175, ZEC=175)


def specification(setup):
    if setup == 1:
        spot = perp = MIXED
        thresholds = {s: 20 if s in MAJORS else 25 for s in MIXED}
        adx = {'spot': thresholds, 'perp': thresholds}
    elif setup in (2, 3):
        spot = perp = ALT
        adx = {'spot': dict.fromkeys(spot, 20)}
        if setup == 2:
            adx['perp'] = dict.fromkeys(perp, 20)
    elif setup == 4:
        spot, perp = PAIR, THIRDS
        adx = {'spot': dict.fromkeys(spot, 20), 'perp': dict.fromkeys(perp, 25)}
    elif setup == 5:
        spot, perp = THIRDS, PAIR
        adx = {'spot': dict.fromkeys(spot, 25), 'perp': dict.fromkeys(perp, 20)}
    else:
        raise ValueError('requires one of the five requested setups')
    return dict(weights=dict(spot), perp_weights=dict(perp), adx_by_market=adx,
                entry_cap=D(1) if setup == 3 else D('.6'),
                perp_cap=D(0) if setup == 3 else D('.4'), include_perp=setup != 3)


class ADXSetupConfig(HistoricalConfig):
    universe: Literal['setups'] = 'setups'
    setup: Literal[1, 2, 3, 4, 5]
    filter_level: Literal[4] = 4
    entry_window: Literal[30] = 30
    exit_window: Literal[10] = 10
    adx_threshold: Literal[20] = 20  # Compatibility only; the explicit map governs entries.
    adx_by_market: dict[str, dict[str, Literal[20, 25]]]
    perp_cap: D = Field(ge=0, le=1)
    reserve: Literal[0] = 0
    leverage: Literal[1] = 1
    trend_filter: Literal[False] = False
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_stop: Literal['atr14-3x'] = 'atr14-3x'

    @model_validator(mode='after')
    def locked_setup(self):
        if not matches(self, specification(self.setup)):
            raise ValueError('requested ADX setup allocation/threshold/risk rules changed')
        return self


def matches(config, expected):
    """Allocation/threshold map as specified and the Setup risk rules unchanged."""
    return (all(getattr(config, k) == v for k, v in expected.items()) and
            config.capital == 1000 and config.daily_loss == D('.03') and
            config.perp_daily_policy == 'disabled' and config.perp_trade_exit == 'baseline' and
            config.perp_size == 'full' and config.perp_trailing_interval == '4h')


CANDIDATES = ('BTC', 'ETH', 'NEAR', 'SOL', 'ZEC')
BASKETS = tuple(combinations(CANDIDATES, 3))


def basket_specification(basket):
    """Setup-2 rules (A4, ADX20 both markets, Spot60/Short40) on equal thirds of three coins."""
    if tuple(basket) not in BASKETS:
        raise ValueError('requires three distinct coins of BTC/ETH/NEAR/SOL/ZEC in order')
    thirds = weights(**dict.fromkeys(basket, 1))
    adx = dict.fromkeys(thirds, 20)
    return dict(weights=dict(thirds), perp_weights=dict(thirds), adx_by_market={'spot': adx, 'perp': dict(adx)},
                entry_cap=D('.6'), perp_cap=D('.4'), include_perp=True)


class BasketConfig(HistoricalConfig):
    universe: Literal['baskets'] = 'baskets'
    basket: tuple[str, str, str]
    filter_level: Literal[4] = 4
    entry_window: Literal[30] = 30
    exit_window: Literal[10] = 10
    adx_threshold: Literal[20] = 20  # Compatibility only; the explicit map governs entries.
    adx_by_market: dict[str, dict[str, Literal[20]]]
    perp_cap: D = Field(ge=0, le=1)
    reserve: Literal[0] = 0
    leverage: Literal[1] = 1
    trend_filter: Literal[False] = False
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_stop: Literal['atr14-3x'] = 'atr14-3x'

    def locked_overrides(self):
        """Pre-registered variants replace individual locked entries here; the default is none."""
        return {}

    @model_validator(mode='after')
    def locked_basket(self):
        if not matches(self, {**basket_specification(self.basket), **self.locked_overrides()}):
            raise ValueError('three-coin basket allocation/threshold/risk rules changed')
        return self


class BasketStressConfig(BasketConfig):
    """Same basket with every fill's fee and slippage doubled; funding unchanged."""
    cost_multiplier: Literal[2] = 2


class H1BasketConfig(BasketConfig):
    """The Setup-2 rule with every window counted in H1 bars (Donchian 30/10, EMA200/50, ADX14, volume MA20,
    120-bar profile, ATR14 x3). The one pre-registered change from `BasketConfig`; nothing else moves."""
    bar_interval: Literal['1h'] = '1h'


class H1BasketStressConfig(H1BasketConfig):
    cost_multiplier: Literal[2] = 2


class H1GeometryConfig(H1BasketConfig):
    """H1 entries with the H4 price distances: ATR x6 stop/trail and a 40-bar exit channel.
    Volatility scales with the square root of time, so H1 ATR x6 ~ H4 ATR x3; 40 H1 bars = 10 H4 bars."""
    stop_atr_multiple: Literal[6] = 6
    exit_window: Literal[40] = 40


class H1RegimeConfig(H1BasketConfig):
    """H1 triggers; EMA200/50 and ADX/DMI14 are read from the last closed H4 bar."""
    regime_interval: Literal['4h'] = '4h'


class H1FastEmaConfig(H1BasketConfig):
    """EMA50 as the trend line and EMA20 as the rising momentum line, in place of EMA200/EMA50."""
    ema_fast: Literal[20] = 20
    ema_slow: Literal[50] = 50


class H1Adx30Config(H1BasketConfig):
    """Only the ADX threshold moves, 20 -> 30, in both markets."""
    adx_by_market: dict[str, dict[str, Literal[30]]]

    def locked_overrides(self):
        return {'adx_by_market': {m: dict.fromkeys(self.weights, 30) for m in ('spot', 'perp')}}


class H1SlowRegimeConfig(H1BasketConfig):
    """The H4 regime filter re-expressed on H1 bars with the same clock length: EMA200/EMA50 of H4 become
    EMA800/EMA200 and ADX/DMI14 becomes 56. Triggers, volume, profile and stops stay as in the H1 baseline.
    Needs 1,000 h of H1 history before the window start."""
    ema_fast: Literal[200] = 200
    ema_slow: Literal[800] = 800
    adx_period: Literal[56] = 56
    warmup_hours: Literal[1000] = 1000


def _stress(cls):
    class Stressed(cls):
        cost_multiplier: Literal[2] = 2
    Stressed.__name__ = cls.__name__.replace('Config', 'StressConfig')
    return Stressed


H1GeometryStressConfig = _stress(H1GeometryConfig)
H1RegimeStressConfig = _stress(H1RegimeConfig)
H1FastEmaStressConfig = _stress(H1FastEmaConfig)
H1Adx30StressConfig = _stress(H1Adx30Config)
H1SlowRegimeStressConfig = _stress(H1SlowRegimeConfig)


def basket_name(basket):
    return '-'.join(basket)


def basket_cases(start, end):
    """Setup-2 control, then every basket at normal and doubled costs."""
    control = ADXSetupConfig(start=start, end=end, setup=2, **specification(2))
    return [('Setup-2', control)] + [
        (basket_name(b)+suffix, cls(start=start, end=end, basket=b, **basket_specification(b)))
        for b in BASKETS for suffix, cls in (('', BasketConfig), ('-cost2x', BasketStressConfig))]


H1_BASKET = ('ETH', 'NEAR', 'SOL')


def h1_cases(start, end):
    """Pre-registered: the frozen ETH-NEAR-SOL H4 control and its H1 counterpart, each at normal and doubled costs."""
    spec = basket_specification(H1_BASKET)
    name = basket_name(H1_BASKET)
    return [(f'{name}-H4', BasketConfig(start=start, end=end, basket=H1_BASKET, **spec)),
            (f'{name}-H4-cost2x', BasketStressConfig(start=start, end=end, basket=H1_BASKET, **spec)),
            (f'{name}-H1', H1BasketConfig(start=start, end=end, basket=H1_BASKET, **spec)),
            (f'{name}-H1-cost2x', H1BasketStressConfig(start=start, end=end, basket=H1_BASKET, **spec))]


def h1_family_cases(start, end):
    """H4 control and H1 baseline, then four one-concept H1 candidates, each at normal and doubled costs.
    Ordered by indicator preparation so the study prepares each feature set once."""
    spec = basket_specification(H1_BASKET)
    name = basket_name(H1_BASKET)
    adx30 = {m: dict.fromkeys(spec['weights'], 30) for m in ('spot', 'perp')}
    rows = [('H4', BasketConfig, BasketStressConfig, {}),
            ('H1', H1BasketConfig, H1BasketStressConfig, {}),
            ('H1-GEOM', H1GeometryConfig, H1GeometryStressConfig, {}),
            ('H1-ADX30', H1Adx30Config, H1Adx30StressConfig, {'adx_by_market': adx30}),
            ('H1-EMA50-20', H1FastEmaConfig, H1FastEmaStressConfig, {}),
            ('H1-REGIME', H1RegimeConfig, H1RegimeStressConfig, {})]
    cases = []
    for label, normal, stressed, override in rows:
        for suffix, cls in (('', normal), ('-cost2x', stressed)):
            cases.append((f'{name}-{label}{suffix}',
                          cls(start=start, end=end, basket=H1_BASKET, **{**spec, **override})))
    return cases


def h1_slowregime_cases(start, end):
    """H4 control, H1 baseline, the hybrid H4-regime candidate and its pure-H1 counterpart, each at two costs."""
    spec = basket_specification(H1_BASKET)
    name = basket_name(H1_BASKET)
    rows = [('H4', BasketConfig, BasketStressConfig), ('H1', H1BasketConfig, H1BasketStressConfig),
            ('H1-REGIME', H1RegimeConfig, H1RegimeStressConfig),
            ('H1-SLOWREGIME', H1SlowRegimeConfig, H1SlowRegimeStressConfig)]
    return [(f'{name}-{label}{suffix}', cls(start=start, end=end, basket=H1_BASKET, **spec))
            for label, normal, stressed in rows for suffix, cls in (('', normal), ('-cost2x', stressed))]


def setup_cases(start, end):
    return [(f'Setup-{i}', ADXSetupConfig(start=start, end=end, setup=i, **specification(i)))
            for i in range(1, 6)]


def market_weights(config, market):
    return config.weights if market == 'spot' else config.perp_weights if config.include_perp else {}


def adx_threshold(config, market, symbol):
    mapping = getattr(config, 'adx_by_market', None)
    return mapping[market][symbol] if mapping is not None else getattr(config, 'adx_threshold', 25)


def allocation_text(config):
    return '; '.join(f'{market} {cap*100}% '+', '.join(
        f'{s.removesuffix("USDT")} {w*100}% ADX{adx_threshold(config, market, s)}'
        for s, w in market_weights(config, market).items())
        for market, cap in [('spot', config.entry_cap), ('perp', config.perp_cap)] if cap)
