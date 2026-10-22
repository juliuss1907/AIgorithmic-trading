from datetime import datetime, timedelta, timezone

import pytest

from intraday.replay_v2 import slot_replay as sr
from intraday.replay_v2.slot_replay import Entry, Route, Trail

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
FLAT = (100.0, 100.5, 99.5, 100.0)


def at(i, minutes=240):
    return (T0+timedelta(minutes=minutes*i)).isoformat()


def obs(exit=False, **numbers):
    s = dict(mult=1.0, atr=2.0, adx=30.0, plus_di=30.0, minus_di=10.0, volume=150.0, volume_ma=100.0)
    s.update(numbers)
    return dict(s, exit=exit)


def route(bars, observations, market='spot', symbol='ETHUSDT'):
    bars = tuple((at(i), *ohlc) for i, ohlc in enumerate(bars))
    return Route(symbol, market, sr.SIDES[market], bars, tuple(observations), {b[0]: i for i, b in enumerate(bars)})


def once(bar, entry):
    """A plan taking `entry` on closed bar `bar` only."""
    return lambda rt, i: entry if i == bar else None


def test_a_long_limit_works_for_several_bars_and_fills_on_m15_below_the_level():
    bars = [FLAT, FLAT, (100.0, 100.5, 99.5, 100.2), (100.2, 100.6, 99.4, 99.8), FLAT, FLAT]
    r = route(bars, [obs(), obs(), obs(), obs(), obs(exit=True), obs()])
    quarters = [(at(32+k, 15), 100.0, 100.2, 99.8, 100.0) for k in range(32)]
    quarters[20] = (quarters[20][0], 99.9, 100.0, 99.4, 99.6)       # the second H4 bar after the signal dips
    plan = once(1, Entry(1.0, 1, limit=99.5, bars=3, window_key='w'))
    [trade] = sr.run({'ETHUSDT:spot': r}, {'w': quarters}, plan)[0]
    assert trade['ret'] == pytest.approx((100.0/99.5-1)*100-0.3) and trade['reason'] == 'donchian_exit'
    assert trade['signal'] == at(1)


def test_an_unfilled_limit_expires_after_its_bars():
    bars = [FLAT, FLAT, FLAT, FLAT, (100.0, 100.5, 95.0, 96.0), FLAT]
    r = route(bars, [obs() for _ in bars])
    assert sr.run({'ETHUSDT:spot': r}, {}, once(1, Entry(1.0, 1, limit=99.0, bars=2)))[0] == []   # 95 came too late
    [late] = sr.run({'ETHUSDT:spot': r}, {}, once(1, Entry(1.0, 1, limit=99.0, bars=3)))[0]
    assert late['reason'] == 'open'


def test_an_initial_stop_replaces_three_atr_and_stays_capped_at_five_percent():
    assert Trail(1, 100.0, 1.0).stop == 97.0 and Trail(1, 100.0, 2.0).stop == 95.0    # 3 ATR, or the cap
    assert Trail(1, 100.0, 2.0, stop=98.0).stop == 98.0
    assert Trail(1, 100.0, 2.0, stop=90.0).stop == 95.0
    assert Trail(-1, 100.0, 2.0, stop=103.0).stop == 103.0 and Trail(-1, 100.0, 2.0, stop=110.0).stop == 105.0
    bars = [FLAT, FLAT, FLAT, (100.0, 100.5, 97.5, 98.0), FLAT]
    r = route(bars, [obs() for _ in bars])
    [trade] = sr.run({'ETHUSDT:spot': r}, {}, once(1, Entry(1.0, 1, stop=98.0)))[0]
    assert trade['reason'] == 'stop' and trade['ret'] == pytest.approx(-2.0-0.3)


def test_a_late_entry_is_ranked_and_sized_on_its_rank_bar_but_booked_under_the_breakout():
    bars = [FLAT]*6
    observations = [obs(), obs(mult=1.0), obs(), obs(mult=0.5), obs(), obs()]
    r = route(bars, observations)
    [trade] = sr.run({'ETHUSDT:spot': r}, {}, once(3, Entry(1.0, 1, rank_index=3)))[0]
    assert trade['signal'] == at(1) and trade['pnl'] == pytest.approx(-0.3*sr.SLEEVE/sr.SLOTS*0.5)
