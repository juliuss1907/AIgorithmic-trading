import random
from datetime import datetime, timedelta, timezone

import pytest

from intraday.replay_v2 import slot_replay
from intraday.replay_v2 import sr_zone_study as st
from intraday.replay_v2.slot_replay import Route

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
LONG, SHORT = 1, -1
ZONE = (105.0, 106.0, 50)                     # a resistance above a long breakout closing at 104.5
FLAT = (104.0, 104.6, 103.8, 104.5)


def at(i, minutes=240):
    return (T0+timedelta(minutes=minutes*i)).isoformat()


def obs(entry=False, exit=False, blockers=(), **numbers):
    s = dict(mult=1.0, atr=2.0, adx=30.0, plus_di=30.0, minus_di=10.0, volume=150.0, volume_ma=100.0)
    s.update(numbers)
    return dict(s, donchian=entry, failed=frozenset(), blockers=tuple(blockers), exit=exit)


def route(bars, observations, market='spot', symbol='ZECUSDT'):
    bars = tuple((at(i), *ohlc) for i, ohlc in enumerate(bars))
    return Route(symbol, market, slot_replay.SIDES[market], bars, tuple(observations),
                 {b[0]: i for i, b in enumerate(bars)})


def test_a_blocking_zone_holds_the_close_or_starts_within_one_atr_ahead():
    assert st.blocking((ZONE,), LONG, 104.5, 2.0) == ZONE           # 0.5 away, under 1 ATR
    assert st.blocking((ZONE,), LONG, 105.5, 2.0) == ZONE           # inside
    assert st.blocking((ZONE,), LONG, 102.0, 2.0) is None           # 3.0 away
    assert st.blocking((ZONE,), LONG, 107.0, 2.0) is None           # behind a long
    assert st.blocking(((98.0, 99.0, 50),), SHORT, 99.5, 2.0) == (98.0, 99.0, 50)
    assert st.blocking(((98.0, 99.0, 50),), SHORT, 101.5, 2.0) is None


def waiting_route(closes, exit_at=None):
    """A long breakout at bar 1 closing 104.5 under ZONE, then bars closing at `closes`."""
    bars = [FLAT, FLAT]+[(c-0.2, c+0.3, c-0.4, c) for c in closes]
    observations = [obs(), obs(entry=True)]+[obs(exit=i+2 == exit_at) for i in range(len(closes))]
    return route(bars, observations)


def test_s1_skips_a_blocked_breakout_and_b_takes_it():
    r = waiting_route([104.0]*3)
    zones = [(ZONE,)]*len(r.bars)
    assert st.plans(r, zones, 'S1') == {}
    assert st.plans(r, zones, 'B') == {1: slot_replay.Entry(1.0, 1, window_key=st.window(r, 1))}
    assert st.plans(r, [()]*len(r.bars), 'S1')[1].signal_index == 1


def test_s2_enters_after_the_first_close_beyond_the_zone_and_gives_up_on_an_exit_or_after_six_bars():
    r = waiting_route([105.5, 106.5, 107.0])                   # bar 3 closes above 106
    zones = [(ZONE,)]*len(r.bars)
    [(k, e)] = st.plans(r, zones, 'S2').items()
    assert (k, e.signal_index, e.rank_index, e.limit) == (3, 1, 3, None)
    assert st.plans(waiting_route([105.5, 106.5], exit_at=2), zones, 'S2') == {}
    late = waiting_route([104.0]*6+[107.0])                     # bar 8 is the seventh bar after the breakout
    assert st.plans(late, [(ZONE,)]*len(late.bars), 'S2') == {}
    # A new zone within 1 ATR above bar 3's close keeps it waiting; bar 4 clears that one too.
    r = waiting_route([105.5, 106.5, 109.0])
    zones = [(ZONE,)]*3+[(ZONE, (107.0, 108.0, 40))]*3
    assert list(st.plans(r, zones, 'S2')) == [4]


def test_s2_limit_mode_prices_a_perp_short_from_the_clearing_bar():
    bars = [(100.0, 100.2, 99.4, 99.5)]*2+[(99.0, 99.2, 97.0, 97.5)]+[(97.5, 97.8, 97.2, 97.5)]
    r = route(bars, [obs(), obs(entry=True), obs(atr=1.0), obs()], market='perp')
    zones = [((98.5, 99.0, 50),)]*len(bars)                    # 0.5 under the breakout close of 99.5
    [(k, e)] = st.plans(r, zones, 'S2', mode='limit').items()
    assert k == 2 and e.limit == pytest.approx(97.5+0.25*1.0) and e.window_key == st.window(r, 1)


def test_s3_works_a_limit_at_the_cleared_edge_with_a_stop_beyond_the_zone():
    r = waiting_route([106.5, 107.0, 106.8, 105.9, 106.0, 106.2])
    zones = [(ZONE,)]*len(r.bars)
    [(k, e)] = st.plans(r, zones, 'S3').items()
    assert (k, e.limit, e.bars, e.stop) == (2, 106.0, 6, pytest.approx(105.0-0.25*2.0))
    trades, _ = slot_replay.run({'ZECUSDT:spot': r}, {}, lambda rt, i: st.plans(r, zones, 'S3').get(i))
    [trade] = trades                                            # bar 5 opens at 105.7, under the limit
    assert trade['signal'] == at(1) and trade['ret'] == pytest.approx((106.2/105.7-1)*100-0.3)


def test_s4_stops_beyond_the_zone_behind_and_falls_back_to_three_atr():
    support = (101.0, 102.0, 50)
    assert st.zone_stop((support,), LONG, 104.5, 2.0) == pytest.approx(100.5)
    assert st.zone_stop(((95.0, 96.0, 50),), LONG, 104.5, 2.0) is None          # 9.6% away, over the cap
    assert st.zone_stop((), LONG, 104.5, 2.0) is None
    r = waiting_route([104.0]*3)
    [e] = st.plans(r, [(ZONE, support)]*len(r.bars), 'S4').values()
    assert e.stop == pytest.approx(100.5)                     # S4 keeps the blocked breakout, as B does


def test_criteria_six_and_seven_need_every_robustness_run_ahead_of_b():
    def side(total, dd, halves=(1.0, 1.0)):
        return dict(total=total, drawdown=dd, ratio=total/dd, halves=list(halves))

    base = dict(market=side(39, 7.8, (20, 19)), limit=side(43, 5.8), double=side(20, 9), no_late_zec=35,
                without={'ETHUSDT': 30, 'ZECUSDT': 25})
    good = dict(market=side(45, 7, (22, 23)), limit=side(47, 5), double=side(25, 9), no_late_zec=40,
                without={'ETHUSDT': 33, 'ZECUSDT': 30})
    assert all(st.criteria(good, base).values())
    assert st.criteria(dict(good, no_late_zec=34), base)['6_without_late_zec'] is False
    assert st.criteria(dict(good, without={'ETHUSDT': 33, 'ZECUSDT': 24}), base)['7_each_coin_left_out'] is False


def test_cut_and_left_out_coins_keep_routes_and_zones_aligned():
    r = waiting_route([104.0]*3)
    routes = {'ZECUSDT:spot': r, 'ETHUSDT:spot': route([b[1:] for b in r.bars], r.obs, symbol='ETHUSDT')}
    zones = {rt: [()]*len(r.bars) for rt in routes}
    kept, kept_zones = st.without_coin(routes, zones, 'ETHUSDT')
    assert list(kept) == ['ZECUSDT:spot'] and kept_zones == {'ZECUSDT:spot': zones['ZECUSDT:spot']}
    cut = st.cut(r, at(2))
    assert len(cut.bars) == 2 and cut.index == {at(0): 0, at(1): 1}


def test_part0_counts_a_turn_a_cross_and_draws_placebos_off_the_real_zones():
    bars = [FLAT, (104.5, 105.5, 104.4, 104.8), (104.8, 104.9, 103.5, 103.6),   # touch, then 103.6 < 105-1
            FLAT, (104.5, 106.5, 104.4, 106.4)]                               # touch and close above 106
    r = route(bars, [obs() for _ in bars])
    assert st.touches(r, [(ZONE,)]*len(bars)) == {'turned': 1, 'crossed': 1}
    one = st.placebo_series(r, [(ZONE,)]*len(bars), random.Random(st.SEED))
    two = st.placebo_series(r, [(ZONE,)]*len(bars), random.Random(st.SEED))
    assert one == two and len(one[0]) == 1
    lo, hi, _ = one[0][0]
    assert hi-lo == pytest.approx(1.0) and (hi < ZONE[0] or lo > ZONE[1])
