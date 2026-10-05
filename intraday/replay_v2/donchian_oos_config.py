"""Research-only cost stress. Legacy FilterConfig serialization remains unchanged."""

from datetime import datetime, timezone
from typing import Literal

from intraday.replay_v2.donchian_filter_book import FilterConfig


START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 10, 28, 20, tzinfo=timezone.utc)


class OOSConfig(FilterConfig):
    cost_multiplier: Literal[1, 2] = 1


def cases(start=START, end=END):
    return [(f'A{level}-Donchian30-10', OOSConfig(start=start, end=end,
             entry_window=30, exit_window=10, filter_level=level)) for level in range(5)] + [
        ('A4-Donchian20-10', OOSConfig(start=start, end=end,
          entry_window=20, exit_window=10, filter_level=4)),
        ('A4-Donchian30-10-cost2', OOSConfig(start=start, end=end,
          entry_window=30, exit_window=10, filter_level=4, cost_multiplier=2))]
