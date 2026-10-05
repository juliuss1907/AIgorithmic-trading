"""Five locked research variants; legacy FilterConfig serialization stays intact."""

from datetime import datetime, timezone
from typing import Literal

from intraday.replay_v2.donchian_filter_book import FilterConfig


START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 2, tzinfo=timezone.utc)


class ADXStudyConfig(FilterConfig):
    filter_level: Literal[4] = 4
    entry_window: Literal[30] = 30
    exit_window: Literal[10] = 10
    adx_threshold: Literal[15, 18, 20, 25] | None = 25


def cases(start=START, end=END):
    return [(f'A4-Donchian30-10-ADX{threshold if threshold is not None else "off"}',
             ADXStudyConfig(start=start, end=end, adx_threshold=threshold))
            for threshold in (None, 20, 18, 15, 25)]
