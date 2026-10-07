"""Five locked research variants; legacy FilterConfig serialization stays intact."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import Field

from intraday.replay_v2.donchian_filter_book import FilterConfig


START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 2, tzinfo=timezone.utc)


class ADXStudyConfig(FilterConfig):
    filter_level: Literal[4] = 4
    entry_window: Literal[30] = 30
    exit_window: Literal[10] = 10
    adx_threshold: Literal[15, 18, 20, 25] | None = 25


FIVE_WEIGHTS = dict(zip(('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'NEARUSDT', 'ZECUSDT'),
                        map(Decimal, ('.4', '.2', '.2', '.1', '.1'))))


class FiveCoinADXConfig(ADXStudyConfig):
    universe: Literal['five'] = 'five'
    study_weights: ClassVar[dict[str, Decimal]] = FIVE_WEIGHTS
    weights: dict[str, Decimal] = Field(default_factory=lambda: dict(FIVE_WEIGHTS))
    perp_weights: dict[str, Decimal] = Field(default_factory=lambda: dict(FIVE_WEIGHTS))


def config_type(universe='three'):
    if universe == 'setups':
        from intraday.replay_v2.donchian_adx_setups import ADXSetupConfig
        return ADXSetupConfig
    if universe not in ('three', 'five'):
        raise ValueError('unknown ADX universe')
    return FiveCoinADXConfig if universe == 'five' else ADXStudyConfig


def cases(start=START, end=END, *, universe='three'):
    if universe == 'setups':
        from intraday.replay_v2.donchian_adx_setups import setup_cases
        return setup_cases(start, end)
    model = config_type(universe)
    return [(f'A4-Donchian30-10-ADX{threshold if threshold is not None else "off"}',
             model(start=start, end=end, adx_threshold=threshold))
            for threshold in (None, 20, 18, 15, 25)]
