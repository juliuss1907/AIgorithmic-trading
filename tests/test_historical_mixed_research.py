from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.replay_v2.contracts import Candle


START = datetime(2026, 9, 30, tzinfo=timezone.utc)
WIDTH = timedelta(hours=4)


def bars(start, count, price=100):
    return tuple(Candle(opened_at=start+i*WIDTH, available_at=start+(i+1)*WIDTH,
        open=price, high=Decimal(price)+1, low=Decimal(price)-1,
        close=price, volume=1) for i in range(count))


def daily_rows(start, count, descending=False):
    rows = []
    for i in range(count):
        at = int((start+timedelta(days=i)).timestamp()*1000)
        price = 200-i if descending else 100+i
        rows.append([at, price, price+1, price-1, price, 1, at+86400000-1])
    return rows


def test_historical_pagination_freezes_exact_native_bars():
    from intraday.replay_v2.historical_data import fetch_candle_snapshot

    expected = bars(START, 5)
    queries = []
    def fetch(path, query):
        queries.append((path, query))
        return [c.row() for c in expected if query['startTime'] <= int(c.opened_at.timestamp()*1000)][:2]
    snapshot = fetch_candle_snapshot('BTCUSDT', '4h', START, START+5*WIDTH,
                                     fetch_json=fetch, page_size=2, now=START+6*WIDTH)
    assert snapshot.candles() == expected
    assert len(queries) == 3
    assert all(path == '/fapi/v1/klines' for path, _ in queries)
    assert snapshot.source.endswith('/fapi/v1/klines')
    changed = snapshot.model_dump()
    changed['raw_rows'] = [list(row) for row in changed['raw_rows']]
    changed['raw_rows'][0][4] = 99
    with pytest.raises(ValueError, match='checksum'):
        type(snapshot).model_validate(changed)


@pytest.mark.parametrize('kind', ['gap', 'duplicate', 'wrong_width', 'nan'])
def test_historical_collection_refuses_bad_or_incomplete_coverage(kind):
    from intraday.replay_v2.historical_data import fetch_candle_snapshot

    rows = [c.row() for c in bars(START, 3)]
    if kind == 'gap':
        rows.pop(1)
    elif kind == 'duplicate':
        rows[1] = rows[0]
    elif kind == 'wrong_width':
        rows[0][6] -= 1
    else:
        rows[0][4] = 'NaN'
    with pytest.raises(ValueError):
        fetch_candle_snapshot('BTCUSDT', '4h', START, START+3*WIDTH,
            fetch_json=lambda path, query: rows, now=START+4*WIDTH)


def test_perp_signal_long_short_mirror_and_no_current_bar_in_channel():
    from intraday.replay_v2.historical_mixed import perp_observation

    rows = [c.row() for c in bars(START-31*WIDTH, 31)]
    rows[-1][2] = rows[-1][4] = 105
    obs = perp_observation(rows)
    assert obs.entry_side == 1 and not obs.exit_long
    rows[-1][2] = 101
    rows[-1][3] = rows[-1][4] = 95
    obs = perp_observation(rows)
    assert obs.entry_side == -1 and obs.exit_long and not obs.exit_short


def test_daily_long_short_filter_only_uses_closed_daily_bars():
    from intraday.replay_v2.historical_mixed import daily_directions

    rows = daily_rows(START-timedelta(days=60), 62)
    times, sides = daily_directions(rows)
    assert sides[49] == 0 and sides[50] == 1
    assert times[59] == START
    _, short = daily_directions(daily_rows(START-timedelta(days=60), 62, True))
    assert short[50] == -1


def test_historical_config_and_stop_distance_are_explicit():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, stop_fraction

    fixed = HistoricalConfig(start=START, end=START+WIDTH, perp_stop='fixed-1pct')
    adaptive = HistoricalConfig(start=START, end=START+WIDTH, perp_stop='atr14-2x')
    assert stop_fraction(fixed, Decimal(100), Decimal(2)) == Decimal('.01')
    assert stop_fraction(adaptive, Decimal(100), Decimal(2)) == Decimal('.04')
    assert fixed.trend_filter and fixed.exit_window == 8
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, perp_stop='unknown')
