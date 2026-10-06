"""Opt-in native research intervals and closed H4/H8 trend consensus."""

from bisect import bisect_right
from datetime import timedelta
from typing import ClassVar, Literal

from intraday.replay_v2.indicators import ema50_trend, trend_side
from intraday.replay_v2.trailing_candle import TrailingCandle, WIDTHS as TRAILING_WIDTHS


WIDTHS = {**TRAILING_WIDTHS, '15m': timedelta(minutes=15), '8h': timedelta(hours=8)}


class IntradayCandle(TrailingCandle):
    interval: Literal['1h', '30m', '15m', '8h']
    widths: ClassVar[dict[str, timedelta]] = WIDTHS


def trend_series(bars):
    """EMA50 seeded by 50 closes; direction requires a known prior EMA."""
    trend = ema50_trend([bar.close for bar in bars])
    return ([bar.available_at for bar in bars],
            [trend_side(bar.close, ema, prior) for bar, (ema, prior) in zip(bars, trend)])


def consensus_at(series, at):
    directions = []
    for times, sides in series:
        index = bisect_right(times, at)-1
        directions.append(sides[index] if index >= 0 else 0)
    return directions[0] if directions and all(s == directions[0] for s in directions) else 0
