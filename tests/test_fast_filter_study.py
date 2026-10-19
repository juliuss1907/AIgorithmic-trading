from datetime import datetime, timedelta, timezone

import pytest

from intraday.replay_v2 import fast_filter_study as ff
from intraday.replay_v2.fast_filter_study import Route

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
SHORT, LONG = -1, 1


def at(i, minutes=240):
    return (T0+timedelta(minutes=minutes*i)).isoformat()


def obs(donchian=False, failed=(), blockers=(), exit=False, **numbers):
    s = dict(mult=1.0, atr=2.0, adx=30.0, prior_adx=28.0, adx3=25.0, plus_di=10.0, minus_di=30.0, volume=150.0,
             volume_ma=100.0, ema20=101.0, prior_ema20=102.0, ema50=103.0)
    s.update(numbers)
    return dict(s, donchian=donchian, failed=frozenset(failed), blockers=tuple(blockers), exit=exit)


def route(bars, observations, market='perp', symbol='ETHUSDT'):
    bars = tuple((at(i), *ohlc) for i, ohlc in enumerate(bars))
    return Route(symbol, market, ff.SIDES[market], bars, tuple(observations), {b[0]: i for i, b in enumerate(bars)})


FLAT = (100.0, 100.5, 99.5, 100.0)


def test_b_v1_v2_size_a_breakout_by_its_filters():
    full, partial = obs(True), obs(True, failed=('volume_ma',))
    bar = (at(0), 100.0, 100.5, 98.0, 98.5)
    assert [ff.entry_factor(v, SHORT, full, bar) for v in ('B', 'V1', 'V2')] == [1.0, 1.0, 1.0]
    assert [ff.entry_factor(v, SHORT, partial, bar) for v in ('B', 'V1', 'V2')] == [0.0, 0.5, 0.5]
    assert ff.entry_factor('V1', SHORT, obs(True, blockers=('profile_incomplete',)), bar) == 0.0
    assert ff.entry_factor('V1', SHORT, obs(False), bar) == 0.0


def test_v3_swaps_the_ema_pair_for_ema20_and_ema50():
    bar = (at(0), 100.0, 100.5, 98.0, 98.5)
    s = obs(True, failed=('ema200', 'ema50'))             # close 98.5 under EMA50 103, under a falling EMA20 101
    assert ff.entry_factor('V3', SHORT, s, bar) == 1.0
    assert ff.entry_factor('V3', SHORT, obs(True, failed=('ema200',), prior_ema20=100.0), bar) == 0.0  # EMA20 rising
    assert ff.entry_factor('V3', SHORT, obs(True, failed=('ema200', 'adx')), bar) == 0.0             # others stay


def test_v4_replaces_the_adx_rule_with_a_three_bar_rise():
    bar = (at(0), 100.0, 100.5, 98.0, 98.5)
    assert ff.entry_factor('V4', SHORT, obs(True, failed=('adx',), adx=15.0, adx3=12.0), bar) == 1.0
    assert ff.entry_factor('V4', SHORT, obs(True, adx=30.0, adx3=31.0), bar) == 0.0


def test_v5_needs_a_breakout_body_of_one_atr_in_place_of_ema50_and_adx_dmi():
    strong, weak = (at(0), 100.0, 100.2, 97.5, 98.0), (at(0), 99.0, 99.5, 97.5, 98.0)
    s = obs(True, failed=('ema50', 'adx', 'dmi'))
    assert ff.entry_factor('V5', SHORT, s, strong) == 1.0         # body 2.0 = 1 ATR
    assert ff.entry_factor('V5', SHORT, s, weak) == 0.0           # body 1.0
    assert ff.entry_factor('V5', SHORT, obs(True, failed=('ema200',)), strong) == 0.0


def test_tier2_waits_for_ema_adx_and_dmi_only():
    assert ff.tier2_ready(obs(failed=('volume_ma', 'volume_profile')))
    assert not ff.tier2_ready(obs(failed=('dmi',)))
    assert not ff.tier2_ready(None)


def two_tier_route(confirm_at=2):
    """Short breakout at bar 1 missing volume, EMA/ADX/DMI agree at `confirm_at`, Donchian exit flag at bar 5."""
    observations = [obs(), obs(True, failed=('volume_ma',))]+[obs() for _ in range(2, 7)]
    for i in range(2, min(confirm_at, 7)):
        observations[i] = obs(failed=('adx',))
    observations[5] = obs(exit=True)
    bars = [FLAT, (100.0, 100.5, 98.0, 98.5), (98.0, 98.5, 97.5, 98.0), (97.0, 97.5, 96.5, 97.0),
            (96.0, 96.5, 95.5, 96.0), (96.0, 96.5, 95.5, 96.0), (95.0, 95.5, 94.5, 95.0)]
    return route(bars, observations)


def test_v2_adds_the_second_half_after_the_confirming_bar_and_exits_both_together():
    trades, _ = ff.run({'ETHUSDT:perp': two_tier_route()}, {}, 'V2')
    first, second = sorted(trades, key=lambda t: t['tier'])
    assert (first['tier'], second['tier']) == (1, 2) and first['reason'] == second['reason'] == 'donchian_exit'
    weight = ff.SLEEVE/ff.SLOTS*0.5
    assert first['pnl'] == pytest.approx(((1-95.0/98.0)*100-0.2)*weight)     # in at bar 2's open, out at bar 6's
    assert second['pnl'] == pytest.approx(((1-95.0/97.0)*100-0.2)*weight)    # added at bar 3's open
    [v1] = ff.run({'ETHUSDT:perp': two_tier_route()}, {}, 'V1')[0]
    assert v1['factor'] == 0.5 and ff.run({'ETHUSDT:perp': two_tier_route()}, {}, 'B')[0] == []


def test_v2_drops_the_second_half_when_the_window_passes_unconfirmed():
    trades, _ = ff.run({'ETHUSDT:perp': two_tier_route(confirm_at=99)}, {}, 'V2')
    assert [t['tier'] for t in trades] == [1]


def test_limit_mode_fills_a_perp_short_on_m15_above_the_signal_close():
    observations = [obs(), obs(True), obs(), obs(exit=True), obs()]
    bars = [FLAT, (100.0, 100.5, 98.0, 98.5), (98.5, 99.2, 98.0, 98.2), (98.0, 98.5, 97.5, 98.0), FLAT]
    r = route(bars, observations)
    quarters = [(at(2*16+k, 15), 98.5, 98.6, 98.4, 98.5) for k in range(16)]
    quarters[3] = (quarters[3][0], 98.6, 99.1, 98.5, 98.9)                    # level 98.5 + 0.25 x 2 = 99.0
    [trade] = ff.run({'ETHUSDT:perp': r}, {f'ETHUSDT:{at(1)}': quarters}, 'B', mode='limit')[0]
    assert trade['ret'] == pytest.approx((1-100.0/99.0)*100-0.2)             # out at bar 4's open
    assert ff.run({'ETHUSDT:perp': r}, {}, 'B', mode='limit')[0][0]['ret'] == pytest.approx(
        (1-100.0/99.0)*100-0.2)                                              # no M15: bar 2's high fills it


def test_drawdown_and_criteria():
    trades = [dict(exited=at(i), pnl=p) for i, p in enumerate((2.0, -3.0, 1.0, -1.5, 4.0))]
    assert ff.drawdown(trades) == pytest.approx(3.5)

    def side(total, dd, halves=(1.0, 1.0)):
        return dict(total=total, drawdown=dd, ratio=total/dd, halves=list(halves))

    base = dict(market=side(39, 7.8, (20, 19)), limit=side(43, 5.8), double=side(20, 9))
    good = dict(market=side(45, 8, (22, 23)), limit=side(47, 6), double=side(25, 9))
    assert all(ff.criteria(good, base).values())
    levered = dict(good, limit=side(47, 9.9))
    assert ff.criteria(levered, base)['3_ratio_not_below'] is False
    one_half = dict(good, market=side(45, 8, (25, 18)))
    assert ff.criteria(one_half, base)['4_both_halves'] is False
