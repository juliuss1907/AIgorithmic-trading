"""Opt-in native research intervals and closed H4/H8 trend consensus."""

from bisect import bisect_right
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar, Literal

from intraday.replay_v2.trailing_candle import TrailingCandle, WIDTHS as TRAILING_WIDTHS


WIDTHS = {**TRAILING_WIDTHS, '15m': timedelta(minutes=15), '8h': timedelta(hours=8)}


class IntradayCandle(TrailingCandle):
    interval: Literal['1h', '30m', '15m', '8h']
    widths: ClassVar[dict[str, timedelta]] = WIDTHS


def trend_series(bars):
    """EMA50 seeded by 50 closes; direction requires a known prior EMA."""
    ema, prior, closes, sides = None, None, [], []
    for bar in bars:
        close = bar.close
        closes.append(close)
        if len(closes) == 50:
            ema = sum(closes)/50
        elif ema is not None:
            prior = ema
            ema += Decimal(2)/51*(close-ema)
        sides.append(1 if prior is not None and close > ema > prior else
                     -1 if prior is not None and close < ema < prior else 0)
    return [bar.available_at for bar in bars], sides


def consensus_at(series, at):
    directions = []
    for times, sides in series:
        index = bisect_right(times, at)-1
        directions.append(sides[index] if index >= 0 else 0)
    return directions[0] if directions and all(s == directions[0] for s in directions) else 0
