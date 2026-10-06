from decimal import Decimal as D

from intraday.replay_v2.indicators import ema50_trend, trend_side


def legacy_sides(closes):
    """The inline loop previously copied into each trend filter."""
    seen, ema, prior, sides = [], None, None, []
    for close in closes:
        seen.append(close)
        if len(seen) == 50:
            ema = sum(seen)/50
        elif ema is not None:
            prior = ema
            ema += D(2)/51*(close-ema)
        sides.append(1 if prior is not None and close > ema > prior else
                     -1 if prior is not None and close < ema < prior else 0)
    return sides


def test_ema50_matches_legacy_seed_and_direction():
    closes = [D(100)+D(i)/7 for i in range(80)] + [D(111)-D(i)/3 for i in range(80)]
    trend = ema50_trend(closes)
    assert trend[48] == (None, None)
    assert trend[49] == (sum(closes[:50])/50, None)  # SMA seed, no direction yet.
    assert trend[50][1] == trend[49][0]
    sides = [trend_side(c, e, p) for c, (e, p) in zip(closes, trend)]
    assert sides == legacy_sides(closes)
    assert {1, -1} <= set(sides) and sides[:50] == [0]*50


def test_trend_side_requires_prior_and_ordered_ema():
    assert trend_side(D(10), D(9), None) == 0
    assert trend_side(D(10), D(9), D(8)) == 1
    assert trend_side(D(7), D(8), D(9)) == -1
    assert trend_side(D(10), D(9), D(9.5)) == 0
