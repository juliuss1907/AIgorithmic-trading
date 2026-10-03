from datetime import timedelta

import pytest

from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from test_historical_mixed_research import START


@pytest.mark.parametrize('interval,minutes', [('1h', 60), ('30m', 30)])
def test_native_finer_snapshot_paginates_and_keeps_h4_contract(interval, minutes):
    from intraday.replay_v2.trailing_candle import TrailingCandle
    width = minutes*60*1000
    opening = int(START.timestamp()*1000)
    rows = [[opening+i*width, '100', '101', '99', '100', '1', opening+(i+1)*width-1]
            for i in range(4)]
    calls = []
    def fetch(path, query):
        calls.append(query)
        return [r for r in rows if r[0] >= query['startTime']][:2]
    snapshot = fetch_candle_snapshot('BTC', interval, START, START+timedelta(minutes=4*minutes),
        fetch_json=fetch, page_size=2, now=START+timedelta(days=1))
    assert snapshot.pages == len(calls) == 2
    assert len(snapshot.candles()) == 4
    assert all(isinstance(c, TrailingCandle) and c.interval == interval for c in snapshot.candles())
    assert snapshot.candles()[0].row() == rows[0]
    with pytest.raises(ValueError):
        Candle.from_row(rows[0])
    with pytest.raises(ValueError):
        TrailingCandle.from_row(rows[0], '4h')


@pytest.mark.parametrize('bad', ['gap', 'duplicate', 'ohlc', 'nan', 'clock'])
def test_finer_input_validation_rejects_corrupt_evidence(bad):
    from intraday.replay_v2.historical_data import validate_rows
    opening = int(START.timestamp()*1000)
    rows = [[opening+i*3600000, '100', '101', '99', '100', '1', opening+(i+1)*3600000-1]
            for i in range(2)]
    if bad == 'gap':
        rows.pop()
    elif bad == 'duplicate':
        rows[1] = rows[0]
    elif bad == 'ohlc':
        rows[0][2] = '98'
    elif bad == 'nan':
        rows[0][4] = 'NaN'
    else:
        rows[0][6] += 1
    with pytest.raises(ValueError):
        validate_rows(rows, '1h', START, START+timedelta(hours=2))


def test_finer_bundle_checks_identity_checksum_and_frozen_h4_boundaries(tmp_path):
    from intraday.replay_v2.trailing_data import collect_inputs, decode_inputs
    from intraday.replay_v2.historical_mixed import HistoricalConfig
    from intraday.replay_v2.metrics import fingerprint
    from test_historical_mixed_research import fixture_inputs, WIDTH
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    _, _, perp, _ = fixture_inputs(cfg)
    def fetch(symbol, interval, start, end, **kw):
        minutes = 60 if interval == '1h' else 30
        width = minutes*60000
        opening = int(start.timestamp()*1000)
        rows = [[opening+i*width, '100', '101', '99', '100', '1', opening+(i+1)*width-1]
                for i in range(int((end-start)/timedelta(minutes=minutes)))]
        return fetch_candle_snapshot(symbol, interval, start, end, fetch_json=lambda p, q: rows,
                                     now=START+timedelta(days=1))
    raw = collect_inputs(cfg, tmp_path, candle_fetcher=fetch)
    decoded = decode_inputs(cfg, raw, perp)
    assert set(decoded) == {'1h', '30m'} and len(decoded['30m']['BTCUSDT']) == 8
    raw['candles']['BTCUSDT']['1h']['raw_rows'][0][1] = '100.5'
    with pytest.raises(ValueError, match='checksum'):
        decode_inputs(cfg, raw, perp)
    snapshot = raw['candles']['BTCUSDT']['1h']
    snapshot['snapshot_id'] = fingerprint({k: v for k, v in snapshot.items() if k != 'snapshot_id'})
    raw['bundle_checksum'] = fingerprint({k: v for k, v in raw.items() if k != 'bundle_checksum'})
    with pytest.raises(ValueError, match='boundary'):
        decode_inputs(cfg, raw, perp)
