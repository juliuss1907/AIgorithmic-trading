from datetime import timedelta

import pytest

from intraday.replay_v2.historical_data import fetch_candle_snapshot
from test_historical_mixed_research import START


def native_bundle(config, root):
    from intraday.replay_v2.intraday_data import collect_inputs
    from intraday.replay_v2.historical_data import WIDTHS
    def fetch(symbol, interval, start, end, **kw):
        width = int(WIDTHS[interval].total_seconds()*1000)
        rows = [[at, '100', '101', '99', '100', '1', at+width-1]
                for at in range(int(start.timestamp()*1000), int(end.timestamp()*1000), width)]
        return fetch_candle_snapshot(symbol, interval, start, end, price_kind=kw['price_kind'],
            fetch_json=lambda p,q: rows, now=end+timedelta(days=1))
    return collect_inputs(config, root, candle_fetcher=fetch)


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


def test_intraday_bundle_rejects_hash_warmup_and_boundary_changes(tmp_path):
    from copy import deepcopy
    from intraday.replay_v2.intraday_book import IntradayConfig
    from intraday.replay_v2.intraday_data import decode_inputs
    from intraday.replay_v2.metrics import fingerprint
    from test_historical_mixed_research import fixture_inputs
    cfg = IntradayConfig(start=START, end=START+timedelta(hours=8), trend_filter=False)
    _, _, perp, _ = fixture_inputs(cfg)
    raw = native_bundle(cfg, tmp_path)
    data = decode_inputs(cfg, raw, perp)
    assert len(data['BTCUSDT']['trade1h']) == 31+8
    assert len(data['BTCUSDT']['trade4h']) == 60+2
    assert len(data['BTCUSDT']['trade8h']) == 60+1
    bad = deepcopy(raw)
    snap = bad['candles']['BTCUSDT']['trade15m']
    snap['raw_rows'][0][1] = '100.5'
    with pytest.raises(ValueError, match='checksum'):
        decode_inputs(cfg, bad, perp)
    snap['snapshot_id'] = fingerprint({k:v for k,v in snap.items() if k != 'snapshot_id'})
    bad['bundle_checksum'] = fingerprint({k:v for k,v in bad.items() if k != 'bundle_checksum'})
    with pytest.raises(ValueError, match='boundary'):
        decode_inputs(cfg, bad, perp)
    bad = deepcopy(raw)
    snap = bad['candles']['BTCUSDT']['trade1h']
    snap['raw_rows'].pop(0)
    snap['snapshot_id'] = fingerprint({k:v for k,v in snap.items() if k != 'snapshot_id'})
    bad['bundle_checksum'] = fingerprint({k:v for k,v in bad.items() if k != 'bundle_checksum'})
    with pytest.raises(ValueError, match='gap'):
        decode_inputs(cfg, bad, perp)
