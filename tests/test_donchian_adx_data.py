from datetime import timedelta
import json

import pytest

from intraday.replay_v2.donchian_filter_data import snapshot, SOURCES
from intraday.replay_v2.donchian_adx_config import START


def test_merge_preserves_native_rows_and_deduplicates_exact_overlaps():
    from intraday.replay_v2.donchian_adx_data import merge_rows
    a = [0,'1','2','1','2','3',899999]
    rich = a+['4',5,'6','7','0']
    b = [900000,'2','3','1','2','3',1799999]
    assert merge_rows([a],[rich,b]) == [rich,b]
    assert a == [0,'1','2','1','2','3',899999]
    with pytest.raises(ValueError,match='conflict'):
        merge_rows([a],[[0,'9',*a[2:]]])
    with pytest.raises(ValueError,match='duplicate'):
        merge_rows([a,a])


def test_joined_mark_funding_reference_is_rebound_to_real_native_rows():
    from intraday.replay_v2.donchian_adx_data import merge_funding
    from intraday.replay_v2.donchian_funding_reference import derive, decode
    from intraday.replay_v2.funding import SOURCE
    end = START+timedelta(hours=16)
    rows = [[int((START+timedelta(minutes=15*i)).timestamp()*1000),
             '100','101','99','100','0',int((START+timedelta(minutes=15*(i+1))).timestamp()*1000)-1]
            for i in range(64)]
    mark = snapshot(market='mark',symbol='BTCUSDT',interval='15m',source=SOURCES['mark'],
                    coverage_start=START.isoformat().replace('+00:00','Z'),
                    coverage_end=end.isoformat().replace('+00:00','Z'),
                    fetched_at=(end+timedelta(days=1)).isoformat().replace('+00:00','Z'),pages=1,raw_rows=rows)
    native = [dict(symbol='BTCUSDT',fundingTime=int(START.timestamp()*1000)+6,
                   fundingRate='.0001',markPrice='')]
    early = derive('BTCUSDT',native,START,START+timedelta(hours=8),end,1,mark)
    late = dict(history={'symbol':'BTCUSDT','source':SOURCE},pages=1,
                fetched_at=(end+timedelta(days=1)).isoformat(),
                raw_rows=[dict(symbol='BTCUSDT',fundingTime=int((START+timedelta(hours=8)).timestamp()*1000),
                               fundingRate='.0002',markPrice='100')])
    joined = merge_funding('BTCUSDT',early.model_dump(mode='json'),late,START,end,mark)
    assert joined.raw_rows[0]['markPrice'] == ''
    assert joined.history.settlements[0].rate == early.history.settlements[0].rate
    assert joined.quote_lineage == early.quote_lineage
    assert decode(joined.model_dump(mode='json'),mark).history.mark_snapshot_id == mark.snapshot_id


def test_collector_joins_without_reset_or_synthetic_mark_rows(tmp_path,monkeypatch):
    from datetime import datetime
    import test_donchian_filter_study as fixtures
    from test_donchian_filter_engine import fixture
    from intraday.replay_v2.artifacts import _write
    from intraday.replay_v2.metrics import encoded, fingerprint
    from intraday.replay_v2.historical_data import CandleSnapshot
    from intraday.replay_v2.donchian_filter_data import WARMUP, decode_bundle
    from intraday.replay_v2.donchian_adx_config import ADXStudyConfig
    import intraday.replay_v2.donchian_adx_data as collector
    monkeypatch.setattr(fixtures,'fixture',lambda:fixture(days=2))
    cfg,full = fixtures.bundle()
    join = cfg.start+timedelta(hours=20)
    monkeypatch.setattr(collector,'JOIN',join)

    def sliced(start,end):
        raw=json.loads(json.dumps(full))
        raw['window']={'start':start.isoformat(),'end':end.isoformat()}
        for symbol,tags in raw['candles'].items():
            for tag,p in tags.items():
                first=start if tag=='mark15m' else start-WARMUP[p['interval']]
                p['coverage_start']=first.isoformat().replace('+00:00','Z')
                p['coverage_end']=end.isoformat().replace('+00:00','Z')
                p['raw_rows']=[r for r in p['raw_rows'] if first.timestamp()*1000<=r[0]<end.timestamp()*1000]
                p['snapshot_id']=fingerprint({k:v for k,v in p.items() if k!='snapshot_id'})
            p=raw['funding'][symbol]
            p['history']['coverage_start']=start.isoformat().replace('+00:00','Z')
            p['history']['coverage_end']=end.isoformat().replace('+00:00','Z')
            p['raw_rows']=[r for r in p['raw_rows'] if start.timestamp()*1000<=r['fundingTime']<end.timestamp()*1000]
            p['history']['settlements']=[r for r in p['history']['settlements'] if start<=datetime.fromisoformat(r['at'])<end]
            p['funding_id']=fingerprint({k:v for k,v in p.items() if k!='funding_id'})
        raw['bundle_checksum']=fingerprint({k:v for k,v in raw.items() if k!='bundle_checksum'})
        return raw

    calls=[]
    def fetch(symbol,interval,start,end,*,price_kind):
        calls.append((symbol,interval,start,end,price_kind))
        p=full['candles'][symbol]['mark15m']
        payload={k:v for k,v in p.items() if k not in ('snapshot_id','market')}
        payload.update(price_kind='mark',coverage_start=start.isoformat().replace('+00:00','Z'),
                       coverage_end=end.isoformat().replace('+00:00','Z'),
                       raw_rows=[r for r in p['raw_rows'] if start.timestamp()*1000<=r[0]<end.timestamp()*1000])
        return CandleSnapshot(snapshot_id=fingerprint(payload),**payload)

    monkeypatch.setattr(collector,'fetch_candle_snapshot',fetch)
    early,late=tmp_path/'early.json',tmp_path/'late.json'
    _write(early,encoded(sliced(cfg.start,join)))
    _write(late,encoded(sliced(join+timedelta(hours=4),cfg.end)))
    path=collector.collect_join(early,late,tmp_path/'joined',progress=lambda _:None)
    joined=json.loads(path.read_text())
    assert joined['window']==full['window'] and len(calls)==3
    assert all(call[2:]==(join,join+timedelta(hours=4),'mark') for call in calls)
    data,funding=decode_bundle(ADXStudyConfig(start=cfg.start,end=cfg.end),joined)
    assert all(len(tags['mark15m'])==192 for tags in data.values())
    assert joined['join_lineage']['synthetic_bars']==0
    with pytest.raises(FileExistsError):
        collector.collect_join(early,late,tmp_path/'joined',progress=lambda _:None)
def test_large_bundle_reader_is_scoped_and_bounded(tmp_path, monkeypatch):
    import pytest
    from intraday.replay_v2 import donchian_adx_data as data
    from intraday.replay_v2.artifacts import _write
    path=tmp_path/'large.json';_write(path,'{"a":1}')
    monkeypatch.setattr(data,'JOIN_MAX_BYTES',7)
    assert data.read_join_inputs(path)=={'a':1}
    monkeypatch.setattr(data,'JOIN_MAX_BYTES',6)
    with pytest.raises(ValueError,match='size limit'):
        data.read_join_inputs(path)
