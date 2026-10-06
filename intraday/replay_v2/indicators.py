"""Shared research trend indicators; pure Decimal arithmetic, no I/O."""

from decimal import Decimal


def ema50_trend(closes):
    """EMA50 seeded by the SMA of the first 50 closes, as (ema, prior) per close.

    prior stays None until the second EMA value, so a direction needs 51 closes.
    """
    window, ema, prior, points = [], None, None, []
    for close in closes:
        if ema is None:
            window.append(close)
            if len(window) == 50:
                ema = sum(window)/50
        else:
            prior = ema
            ema += Decimal(2)/51*(close-ema)
        points.append((ema, prior))
    return points


def trend_side(close, ema, prior):
    """1 for close above a rising EMA, -1 below a falling one, else 0."""
    if prior is None:
        return 0
    return 1 if close > ema > prior else -1 if close < ema < prior else 0
