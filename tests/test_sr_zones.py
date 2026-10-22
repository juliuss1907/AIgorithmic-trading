from intraday.replay_v2 import sr_zones as sr


def bars(n, spikes):
    """Flat bars (high 101, low 99) with a spike high or low {index: ('h' | 'l', price)}."""
    highs, lows = [101.0]*n, [99.0]*n
    for i, (kind, price) in spikes.items():
        (highs if kind == 'h' else lows)[i] = price
    return highs, lows


SPIKES = {100: ('h', 110.0), 200: ('l', 90.0), 300: ('h', 110.5)}


def test_zones_start_after_the_300_bar_range_on_the_next_confirmed_pivot():
    series = sr.zones_series(*bars(320, SPIKES))
    assert series[299] == () and series[309] == ()                 # pivots at 100 and 200 came before the range
    # Range 90..110.5 makes zones up to 1.025 wide: the highs 110 and 110.5 share a zone of two pivots plus the
    # two bars touching it (2*20+2); the low 90 stands alone (20+1).
    assert series[310] == ((110.0, 110.5, 42), (90.0, 90.0, 21))
    assert series[319] == series[310]                               # kept until the next pivot


def test_zones_of_a_bar_never_change_when_later_bars_arrive():
    highs, lows = bars(420, SPIKES | {395: ('l', 89.9)})
    full = sr.zones_series(highs, lows)
    assert sr.zones_series(highs[:350], lows[:350]) == full[:350]


def test_pivots_older_than_the_lookback_leave_the_zones():
    series = sr.zones_series(*bars(420, SPIKES | {395: ('l', 89.9)}))
    # At bar 405 the high of bar 100 (confirmed at 110) is 295 bars old: 110.5 stands alone, 89.9 joins 90.
    assert series[405] == ((89.9, 90.0, 42), (110.5, 110.5, 21))


def test_no_zone_below_the_minimum_strength():
    tracker = sr.ZoneTracker()
    for h, l in zip(*bars(320, {})):
        tracker.update(h, l)
    assert tracker.zones == () and tracker.pivots == []


def test_inside_ahead_and_behind_follow_the_trade_direction():
    zones = ((1256.98, 1296.02, 107), (1041.7, 1075.0, 94), (1349.3, 1385.0, 83), (1112.77, 1123.43, 62))
    assert sr.inside(zones, 1279.0) == zones[0] and sr.inside(zones, 1320.0) is None
    assert sr.ahead(zones, 1320.0, 1) == zones[2]                  # long: the nearest zone above
    assert sr.ahead(zones, 1320.0, -1) == zones[0]                 # short: the nearest zone below
    assert sr.behind(zones, 1320.0, 1) == zones[0] and sr.behind(zones, 1320.0, -1) == zones[2]
    assert sr.ahead(zones, 1400.0, 1) is None and sr.ahead(zones, 1000.0, -1) is None
