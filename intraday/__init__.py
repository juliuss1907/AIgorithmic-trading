"""Standalone BTCUSDT perpetual paper-trading research system.

The package intentionally shares no database or execution state with :mod:`lab`.
"""

from intraday.risk import HardRiskPolicy

__all__ = ["HardRiskPolicy"]
