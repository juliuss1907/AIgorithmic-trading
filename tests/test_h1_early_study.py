import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace

from intraday import setup2
from intraday.replay_v2 import h1_early_study as he
from intraday.replay_v2.slot_replay import Route

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def m15_walk(n, seed=7):
    rng, price, out = random.Random(seed), 100.0, []
    for k in range(n):
        opened = T0+k*setup2.M15
        close = price*(1+rng.uniform(-.004, .0042))
        high, low = max(price, close)*(1+rng.uniform(0, .002)), min(price, close)*(1-rng.uniform(0, .002))
        out.append(setup2.Bar(opened, opened+setup2.M15, *(D(f'{v:.4f}') for v in (price, high, low, close)),
                              D(f'{rng.uniform(50, 150):.2f}')))
        price = close
    return out


def h4_of(m15):
    out = []
    for k in range(0, len(m15), 16):
        q = m15[k:k+16]
        out.append(setup2.Bar(q[0].opened_at, q[0].opened_at+setup2.H4, q[0].open, max(b.high for b in q),
                              min(b.low for b in q), q[-1].close, sum((b.volume for b in q), D(0))))
    return out


def test_a_partial_bar_is_the_first_hours_of_its_h4_bar():
    m15 = m15_walk(16)
    [bar] = h4_of(m15)
    p = he.partial(bar, m15, 2)
    assert (p.opened_at, p.open, p.close) == (bar.opened_at, bar.open, m15[7].close)
    assert p.high == max(b.high for b in m15[:8]) and p.low == min(b.low for b in m15[:8])
    assert p.volume == sum((b.volume for b in m15[:8]), D(0))
    assert he.partial(bar, m15, 4) == bar


def test_the_whole_bar_through_the_early_check_is_the_live_observation_of_the_closed_bar():
    m15 = m15_walk(260*16)
    h4 = h4_of(m15)
    series, obs = setup2.series_observations(h4, m15, side=1, anchor=T0, profile=he.PROFILE, warmup_bars=250)
    times = [b.available_at for b in m15]
    for j in (250, 255, 259):
        o = he.observe(series, j, he.partial(series[j], m15[16*j:16*j+16], 4), 4, m15, times, 1, None)
        assert he.flags(o) == he.flags(obs[j]) and o.atr == obs[j].atr and o.features == obs[j].features


def flat(i, close, high=None, low=None):
    opened = T0+i*setup2.H4
    return setup2.Bar(opened, opened+setup2.H4, D(close), D(high or close), D(low or close), D(close), D(1))


def fake(donchian=True, failed=(), blockers=()):
    return SimpleNamespace(donchian_entry=donchian, failed_filters=tuple(failed), blockers=tuple(blockers))


def test_each_variant_takes_its_first_passing_hour_and_only_closes_beyond_the_channel_are_checked():
    series = [flat(i, 100, 101, 99) for i in range(30)]
    quarter = lambda close: setup2.Bar(T0, T0, D(100), D(close), D(99), D(close), D(1))
    closes = {29: [100.5]*4+[101.5]*4+[102]*4+[103]*4}       # hour 1 stays inside, hours 2 and 3 break out
    seen, verdicts = [], {2: fake(failed=['volume_ma']), 3: fake()}

    def check(series, j, bar, h, m15, times, side, own_from):
        seen.append((j, h))
        return verdicts[h]
    found = he.early_triggers(series, lambda b: [quarter(c) for c in closes.get(series.index(b), [100]*16)], [],
                              1, None, start=25, check=check)
    assert seen == [(29, 2), (29, 3)]
    assert {v: (h, o) for v, (h, o) in found[29].items()} == {'E2': (2, verdicts[2]), 'E1': (3, verdicts[3])}


def test_blockers_and_a_missing_breakout_never_pass_and_e2_ignores_only_volume():
    assert he.passes(fake(), 'E1') and he.passes(fake(failed=['volume_ma']), 'E2')
    assert not he.passes(fake(failed=['volume_ma']), 'E1') and not he.passes(fake(failed=['adx']), 'E2')
    assert not he.passes(fake(blockers=['profile_incomplete']), 'E2') and not he.passes(fake(donchian=False), 'E2')
    series = [flat(i, 100, 101, 99) for i in range(26)]
    assert he.beyond(series, 25, D('98.9'), -1) and not he.beyond(series, 25, D(99), -1)


def test_plans_offer_the_breakout_at_the_open_and_the_early_entry_of_the_next_bar():
    s = dict(mult=1.0, atr=2.0, adx=30.0, plus_di=30.0, minus_di=10.0, volume=150.0, volume_ma=100.0,
             donchian=True, failed=frozenset(), blockers=(), exit=False)
    bars = tuple(((T0+i*setup2.H4).isoformat(), 100.0, 101.0, 99.0, 100.0) for i in range(4))
    route = Route('ETHUSDT', 'perp', -1, bars, (None, s, None, None), {b[0]: i for i, b in enumerate(bars)})
    o = SimpleNamespace(features={k: D(2) for k in he.FEATURES}, size_multiplier=D('.5'), donchian_entry=True,
                        failed_filters=(), blockers=(), exit=False)
    triggers = {2: {'E1': (2, o)}}
    assert he.plans(route, triggers, 'B') == {1: (he.Entry(1.0, 1, window_key=he.srz.window(route, 1)),)}
    b, early = he.plans(route, triggers, 'E1')[1]
    assert (early.signal_index, early.early, early.window_key) == (2, 8, he.early_key(route, 2))
    assert early.obs['mult'] == 0.5 and he.plans(route, triggers, 'E2') == he.plans(route, triggers, 'B')
    assert he.plans(route, triggers, 'B', mode='limit')[1][0].limit == 100.0+0.25*2.0
