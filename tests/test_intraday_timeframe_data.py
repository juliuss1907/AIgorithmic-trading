from datetime import timedelta

import pytest

from intraday.replay_v2.historical_data import fetch_candle_snapshot
from test_historical_mixed_research import START


@pytest.mark.parametrize('interval,width', [('15m', timedelta(minutes=15)), ('8h', timedelta(hours=8))])
def test_native_intraday_intervals_are_validated_without_relaxing_h4(interval, width):
    from intraday.replay_v2.contracts import Candle
    ms = int(width.total_seconds()*1000)
    opening = int(START.timestamp()*1000)
    rows = [[opening, '100', '101', '99', '100', '0', opening+ms-1]]
    snap = fetch_candle_snapshot('BTC', interval, START, START+width,
        fetch_json=lambda p,q: rows, now=START+timedelta(days=1))
    assert snap.candles()[0].interval == interval
    with pytest.raises(ValueError):
        Candle.from_row(rows[0])


def test_trend_uses_closed_native_bars_and_requires_consensus():
    from intraday.replay_v2.intraday_timeframes import trend_series, consensus_at
    from intraday.replay_v2.intraday_timeframes import IntradayCandle
    rows = []
    for i in range(61):
        opening = START-timedelta(hours=8*(61-i))
        price = 100+i
        rows.append(IntradayCandle(interval='8h', opened_at=opening,
            available_at=opening+timedelta(hours=8), open=price, high=price+1,
            low=price-1, close=price, volume=1))
    series = trend_series(rows)
    assert consensus_at((series, series), START) == 1
    assert consensus_at((series, series), rows[49].available_at) == 0
    assert consensus_at((series, ([START], [-1])), START) == 0
    assert consensus_at((series, ([START+timedelta(hours=8)], [1])), START) == 0
