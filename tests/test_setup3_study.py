from datetime import datetime, timedelta, timezone

import pytest

from intraday.replay_v2 import setup3_study as s3
from intraday.replay_v2.setup3_study import Bar, Trade, Variant

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
SHORT, LONG = -1, 1
EMA_ZONE = Variant('A', 'ema200', 0.5, 'channel')
CHANNEL_2R = Variant('A', 'channel', 0.0, '2r')


def at(i, minutes=240):
    return (T0+timedelta(minutes=minutes*i)).isoformat()


def bar(i, o, h, l, c, ema50=95.0, ema200=100.0, atr=2.0):
    return Bar(at(i), o, h, l, c, ema50, ema200, atr)


def downtrend(n=31):
    """Flat bars (high 97.5, low 96.5) under a falling EMA200 with EMA50 below it: a short's trend."""
    return [bar(i, 97.0, 97.5, 96.5, 97.0, ema200=100.0+(n-i)*0.1) for i in range(n)]


def short_setup(after, rejection=(98.5, 99.5, 97.0, 97.2)):
    """Rejection at bar 30 (stop 99.5 + 0.5 = 100), fill at bar 31's open, then `after` from bar 31."""
    bars = downtrend()
    bars[30] = bar(30, *rejection)
    return bars+[bar(31+k, *ohlc) for k, ohlc in enumerate(after)]


def quiet(n):
    return [(97.2, 97.8, 96.8, 97.2)]*n


def test_trend_needs_ema50_on_the_side_and_ema200_sloping_that_way():
    bars = downtrend()
    assert s3.in_trend(bars, 10, SHORT)
    assert not s3.in_trend(bars, 10, LONG)
    rising = [bar(i, 97, 97.5, 96.5, 97, ema200=97.0+i*0.1) for i in range(31)]
    assert not s3.in_trend(rising, 10, SHORT)
    assert not s3.in_trend(bars, 3, SHORT)


def test_touch_of_the_ema200_zone_and_of_the_prior_channel():
    bars = short_setup([])
    assert s3.touched(bars, 30, SHORT, EMA_ZONE)                                   # 99.5 >= 100 - 0.5*2
    assert not s3.touched(bars, 30, SHORT, Variant('A', 'ema200', 0.0, '2r'))      # 99.5 < 100
    assert s3.touched(bars, 30, SHORT, CHANNEL_2R)                                 # 99.5 > prior highs 97.5
    assert not s3.touched(short_setup([], rejection=(97.3, 97.4, 96.0, 96.1)), 30, SHORT, CHANNEL_2R)


def test_rejection_is_a_close_back_beyond_ema200_in_the_trade_colour_and_half():
    assert s3.rejected(Bar(at(0), 99, 101, 95, 96, ema200=100), SHORT)
    assert not s3.rejected(Bar(at(0), 99.8, 101, 95, 99.5, ema200=100), SHORT)   # upper half
    assert not s3.rejected(Bar(at(0), 96, 101, 95, 97, ema200=100), SHORT)       # green
    assert not s3.rejected(Bar(at(0), 102, 103, 95, 100.5, ema200=100), SHORT)   # above EMA200
    assert s3.rejected(Bar(at(0), 101, 105, 99, 104, ema200=100), LONG)


def test_short_fills_next_open_and_takes_the_channel_target():
    bars = short_setup([(97.2, 97.6, 96.4, 96.6)])
    [trade] = s3.simulate('X:perp', bars, SHORT, EMA_ZONE)
    assert (trade.signal_at, trade.entered_at, trade.entry) == (at(30), at(31), 97.2)
    assert trade.stop == pytest.approx(100.0) and trade.target == 96.5
    assert (trade.reason, trade.exit, trade.exited_at) == ('target', 96.5, at(32))
    assert trade.net_pct() == pytest.approx((1-96.5/97.2)*100-0.20)
    assert trade.risk_pct == pytest.approx(2.8/97.2*100)


def test_two_r_target():
    [trade] = s3.simulate('X:perp', short_setup(quiet(1)+[(97.0, 97.0, 91.0, 92.0)]), SHORT, CHANNEL_2R)
    assert trade.target == pytest.approx(97.2-2*2.8) and trade.reason == 'target'


def quarters(*lows_highs):
    return [Bar(at(31*16+k, 15), 97.0, h, l, 97.0) for k, (l, h) in enumerate(lows_highs)]


@pytest.mark.parametrize('m15, reason', [
    (quarters((96.4, 97.5), (96.8, 100.5)), 'target'),   # target first
    (quarters((97.0, 100.5), (96.4, 97.5)), 'stop'),     # stop first
    (quarters((96.4, 100.5)), 'stop'),                   # one M15 bar reaches both
    (None, 'stop'),                                      # no M15 data
])
def test_stop_and_target_in_one_h4_bar_are_ordered_on_m15(m15, reason):
    bars = short_setup([(97.2, 100.5, 96.0, 96.6)])
    [trade] = s3.simulate('X:perp', bars, SHORT, EMA_ZONE, quarters_of=lambda b: m15 if b.at == at(31) else None)
    assert trade.reason == reason


def test_gap_through_the_stop_exits_at_the_open():
    [trade] = s3.simulate('X:perp', short_setup(quiet(1)+[(101.0, 101.5, 100.8, 101.2)]), SHORT, EMA_ZONE)
    assert (trade.reason, trade.exit, trade.exited_at) == ('stop_gap', 101.0, at(32))


def test_time_stop_after_thirty_bars():
    [trade] = s3.simulate('X:perp', short_setup(quiet(32)), SHORT, EMA_ZONE)
    assert (trade.reason, trade.exited_at, trade.exit) == ('time', at(61), 97.2)


@pytest.mark.parametrize('rejection, fill', [
    ((98.5, 103.0, 97.0, 97.2), 97.2),   # stop 103.5 is 6.5% away
    ((99.6, 99.5, 98.0, 98.1), 99.3),    # stop 100 is 0.7 < 0.5 ATR away
    ((98.5, 99.5, 97.0, 97.2), 96.3),    # channel low 96.5 is above the fill: no room
])
def test_registered_skips(rejection, fill):
    bars = short_setup([(fill, fill+0.2, fill-0.2, fill)], rejection=rejection)
    assert s3.simulate('X:perp', bars, SHORT, EMA_ZONE) == []


def test_one_position_per_route():
    bars = short_setup(quiet(3))
    bars[32] = bar(32, 98.5, 99.5, 97.0, 97.2)   # a second rejection while the first trade is open
    trades = s3.simulate('X:perp', bars+[bar(35, 97.2, 97.6, 96.0, 96.2)], SHORT, EMA_ZONE)
    assert [t.signal_at for t in trades] == [at(30)]


def test_signals_before_the_own_data_boundary_are_ignored():
    bars = short_setup([(97.2, 97.6, 96.4, 96.6)])
    assert s3.simulate('X:spot', bars, SHORT, EMA_ZONE, own_from=at(31)) == []


def cross_and_retest():
    """EMA50 crosses below EMA200 at bar 5; bar 8 retests EMA200; bar 12 closes above EMA50."""
    rows = []
    for i in range(16):
        ema50 = 101.0 if i < 5 else 99.0
        o, h, l, c = (98.0, 98.6, 97.6, 98.0)
        if i == 8:
            o, h, l, c = 99.5, 100.5, 98.4, 98.6
        if i == 9:
            o = 98.5
        if i == 12:
            o, h, l, c = 98.0, 99.8, 97.9, 99.5
        rows.append(bar(i, o, h, l, c, ema50=ema50, ema200=100.0))
    return rows


def test_cross_then_first_retest_and_exit_on_a_close_beyond_ema50():
    bars = cross_and_retest()
    assert s3.crossed(bars, 5, SHORT) and not s3.crossed(bars, 6, SHORT)
    assert s3.signals(bars, SHORT, s3.VARIANT_B) == [8]
    [trade] = s3.simulate('X:perp', bars, SHORT, s3.VARIANT_B)
    assert (trade.entered_at, trade.entry, trade.stop, trade.target) == (at(9), 98.5, pytest.approx(101.0), None)
    assert (trade.reason, trade.exited_at, trade.exit) == ('ema50', at(13), 98.0)


def test_retest_window_is_thirty_bars():
    bars = [bar(i, 98, 98.6, 97.6, 98, ema50=101.0 if i < 5 else 99.0) for i in range(40)]
    bars[36] = bar(36, 99.5, 100.5, 98.4, 98.6, ema50=99.0)
    assert s3.signals(bars, SHORT, s3.VARIANT_B) == []


def test_short_funding_sensitivity():
    trade = Trade('X:perp', 'A', SHORT, at(0), at(0), at(6), 100.0, 105.0, 90.0, 99.0, 'time')   # 24 hours
    assert trade.net_pct(funding_8h=0.01) == pytest.approx(1.0-0.20+0.03)
    assert trade.net_pct(cost_mult=2) == pytest.approx(1.0-0.40)


def stats(n, mean, se):
    return dict(n=n, mean=mean, se=se)


def test_selection_needs_thirty_trades_and_prefers_fewer_within_one_standard_error():
    assert s3.select({'a': stats(29, 2.0, 0.1)}) is None
    assert s3.select({'a': stats(80, 0.9, 0.3), 'b': stats(40, 0.7, 0.4), 'c': stats(35, 0.5, 0.2)}) == 'b'
    assert s3.select({'a': stats(80, 0.9, 0.1), 'b': stats(40, 0.7, 0.4)}) == 'a'


def trade(coin, entered, ret, side=SHORT, market='perp'):
    entry = 100.0
    exit_ = entry*(1+side*(ret+0.20)/100)
    return Trade(f'{coin}:{market}', 'A', side, entered, entered, entered, entry, entry*1.03, None, exit_, 'time')


def test_criteria():
    coins = ['A', 'B', 'C', 'D', 'E']
    tuning = [trade(c, f'2025-0{k % 9+1}-01T00:00:00', 1.0+0.2*(k % 3)) for k in range(30) for c in coins[:3]]
    holdout = [trade(c, f'2026-0{k % 9+1}-01T00:00:00', 0.5) for k in range(10) for c in coins[:2]]
    verdict, per_coin = s3.criteria(tuning, holdout, 0.1, coins)
    assert verdict['passed'] and per_coin['D'] == 0
    verdict, _ = s3.criteria(tuning, holdout[:10], 0.1, coins)
    assert verdict['2_holdout_n_and_mean'] == 'insufficient' and not verdict['passed']
    verdict, _ = s3.criteria(tuning, holdout, 0.7, coins)
    assert not verdict['6_correlation_below']


def test_portfolio_caps_positions_and_coins():
    rows = [Trade(f'{c}:perp', 'A', SHORT, at(0), at(0), at(5), 100.0, 102.0, None, 98.0, 'time') for c in 'ABCD']
    rows.append(Trade('A:spot', 'A', LONG, at(1), at(1), at(5), 100.0, 98.0, None, 102.0, 'time'))
    book, monthly = s3.portfolio(rows)
    assert book['trades'] == 3
    assert book['total_pct'] == pytest.approx(3*(2.0-0.20)/2.0)
