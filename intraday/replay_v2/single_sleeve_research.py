"""Opt-in full-capital BTC/ETH/SOL research; no deployed allocation changes."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from intraday.replay_v2.contracts import positive_policy_number
from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical
from intraday.replay_v2.intraday_book import IntradayBook, IntradayConfig
from intraday.replay_v2.intraday_engine import simulate_intraday
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.mixed_book import PERP_FEE, PERP_SLIP
from intraday.replay_v2.portfolio_book import ONE, ZERO


WEIGHTS = {'BTCUSDT':Decimal('.50'), 'ETHUSDT':Decimal('.25'), 'SOLUSDT':Decimal('.25')}
VERSION = 'historical-single-sleeve-research-v1.1'


class SpotOnlyConfig(HistoricalConfig):
    weights: dict[str, Decimal] = Field(default_factory=lambda: dict(WEIGHTS))
    perp_weights: dict[str, Decimal] = Field(default_factory=lambda: dict(WEIGHTS))
    entry_cap: Literal[1] = 1
    perp_cap: Literal[0] = 0
    reserve: Literal[0] = 0
    include_perp: Literal[False] = False
    capital_growth: Literal['realized'] = 'realized'
    drawdown_policy: Literal['observe-only'] = 'observe-only'


class PerpOnlyConfig(IntradayConfig):
    weights: dict[str, Decimal] = Field(default_factory=lambda: dict(WEIGHTS))
    perp_weights: dict[str, Decimal] = Field(default_factory=lambda: dict(WEIGHTS))
    entry_cap: Literal[0] = 0
    perp_cap: Literal[1] = 1
    reserve: Literal[0] = 0
    leverage: Literal[3] = 3
    perp_trailing_interval: Literal['15m'] = '15m'
    allocation_basis: Literal['full-sleeve-margin'] = 'full-sleeve-margin'

    @field_validator('capital', 'daily_loss', 'max_drawdown', 'entry_cap')
    @classmethod
    def _numbers(cls, value, info):
        # Zero is valid ONLY for this opt-in disabled Spot allocation.
        return value if info.field_name == 'entry_cap' and value == 0 else positive_policy_number(value)


class PerpOnlyBook(IntradayBook):
    def perp_target(self, symbol, marks):
        # Allocate the whole realized sleeve as collateral, not 1/3 collateral
        # under the legacy notional budget. Locked entry margin stays committed.
        budget = self.perp_budget(marks)
        requested = budget*self.config.perp_weights[symbol]*self.config.leverage
        denominator = ONE/self.config.leverage+PERP_FEE+PERP_SLIP
        target = max(ZERO, min(requested, (budget-self.locked_margin)/denominator,
                               self.free_cash/denominator))
        return self.allocation_base(marks), requested, target


def simulate_single_sleeve(config, spot, daily, perp, funding, native):
    if isinstance(config, PerpOnlyConfig):
        report = simulate_intraday(config, spot, daily, perp, funding, native, book_class=PerpOnlyBook)
        market = 'perp-only'
        report['methodology']['allocation'] = '100% own realized Perp collateral; isolated3x up to3x gross notional; no Spot or reserve; fees and locked margin constrain fills'
        report['methodology']['signals'] = 'Perp closed H1 Donchian30/8; closed H4/H8 EMA50 consensus; ATR14 H1 x3 stop; net trailing arm3%, peak-minus3pp sampled M15; no Jev/LLM'
        report['limitations'] = sorted(set(report['limitations'])-{
            'spot_rule_unchanged_but_shared_cash_and_parent_halts_can_change_spot_fills'})
    elif isinstance(config, SpotOnlyConfig):
        report = simulate_historical(config, spot, daily, perp, funding)
        market = 'spot-control'
        report['methodology']['allocation'] = '100% own realized Spot capital; costs included in cash limit; Donchian30/8 H4/D1 and ATR14 sizing; no Perp or reserve'
    else:
        raise ValueError('single-sleeve requires an explicit full-capital research config')
    report['config']['market'] = market
    report.update(evaluator_version=VERSION, result_id=fingerprint(dict(
        version=VERSION, config=report['config'], data=report['inputs']['dataset_checksum'])))
    report['inputs']['config_checksum'] = fingerprint(report['config'])
    return report
