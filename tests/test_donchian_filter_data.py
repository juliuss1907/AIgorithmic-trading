from datetime import timedelta
from decimal import Decimal as D

import pytest

from test_donchian_filter_book import START
from intraday.replay_v2.donchian_filter_data import FilterSnapshot, fetch_spot_snapshot, decode_bundle
from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.metrics import fingerprint


def test_spot_collection_paginates_strictly_and_freezes_native_m15():
    calls = []
    def fetch(query):
        calls.append(query)
        ms = query['startTime']
        return [[ms, '100','101','99','100','10', ms+899999]]
    snap = fetch_spot_snapshot('BTCUSDT', '15m', START, START+timedelta(minutes=30),
                               fetch_json=fetch, page_size=1, now=START+timedelta(days=1))
    assert snap.market == 'spot' and snap.pages == 2
    assert len(snap.candles()) == 2
    assert calls[1]['startTime'] == calls[0]['startTime']+900000
    bad = snap.model_dump(mode='json')
    bad['raw_rows'][0][5] = '-1'
    bad['snapshot_id'] = fingerprint({k:v for k,v in bad.items() if k != 'snapshot_id'})
    with pytest.raises(ValueError):
        FilterSnapshot.model_validate(bad)


def test_wrong_source_and_unaligned_window_rejected():
    with pytest.raises(ValueError):
        fetch_spot_snapshot('BTCUSDT', '15m', START+timedelta(seconds=1), START+timedelta(hours=1))


def test_bundle_hash_is_required_before_replay():
    cfg = FilterConfig(start=START, end=START+timedelta(hours=4))
    with pytest.raises(ValueError, match='checksum'):
        decode_bundle(cfg, {})


def test_retained_rows_include_all_binance_evidence_columns():
    from types import SimpleNamespace
    from intraday.replay_v2.donchian_filter_data import verify_retained
    ms = int(START.timestamp()*1000)
    parent = SimpleNamespace(raw_rows=((ms,'100','101','99','100','10',ms+899999,'1000',42),))
    changed = SimpleNamespace(raw_rows=((ms,'100','101','99','100','10',ms+899999,'9999',42),))
    with pytest.raises(ValueError,match='parent rows'):
        verify_retained(changed,parent,START,START+timedelta(minutes=15))
