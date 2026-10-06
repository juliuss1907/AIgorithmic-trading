"""Three isolated accounts using setup 2 rules and equal starting capital."""

from decimal import Decimal as D
from typing import Literal

from pydantic import model_validator

from intraday.replay_v2.historical_mixed import HistoricalConfig


class SingleCoinADXConfig(HistoricalConfig):
    universe: Literal['single'] = 'single'
    coin: Literal['SOLUSDT', 'ZECUSDT', 'NEARUSDT']
    filter_level: Literal[4] = 4
    entry_window: Literal[30] = 30
    exit_window: Literal[10] = 10
    adx_threshold: Literal[20] = 20
    adx_by_market: dict[str, dict[str, Literal[20]]]
    entry_cap: D = D('.6')
    perp_cap: D = D('.4')
    reserve: Literal[0] = 0
    leverage: Literal[1] = 1
    include_perp: Literal[True] = True
    trend_filter: Literal[False] = False
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'
    perp_stop: Literal['atr14-3x'] = 'atr14-3x'

    @model_validator(mode='after')
    def locked_account(self):
        expected = {self.coin: D(1)}
        if (self.weights != expected or self.perp_weights != expected or
                self.adx_by_market != {m: {self.coin: 20} for m in ('spot', 'perp')} or
                self.capital != 1000 or self.entry_cap != D('.6') or self.perp_cap != D('.4') or
                self.daily_loss != D('.03') or self.perp_daily_policy != 'disabled' or
                self.perp_trade_exit != 'baseline' or self.perp_size != 'full' or
                self.perp_trailing_interval != '4h'):
            raise ValueError('single-coin setup 2 allocation/threshold/risk rules changed')
        return self


def single_cases(start, end):
    return [(f'Single-{s.removesuffix("USDT")}', SingleCoinADXConfig(
        start=start, end=end, coin=s, weights={s: D(1)}, perp_weights={s: D(1)},
        adx_by_market={m: {s: 20} for m in ('spot', 'perp')}))
        for s in ('SOLUSDT', 'ZECUSDT', 'NEARUSDT')]
