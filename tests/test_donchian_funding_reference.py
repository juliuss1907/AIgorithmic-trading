from datetime import datetime,timedelta,timezone

import pytest

from intraday.replay_v2.donchian_funding_reference import derive,decode,ReferenceFundingSnapshot
from intraday.replay_v2.funding import FundingSnapshot
from intraday.replay_v2.donchian_filter_data import snapshot,SOURCES
from intraday.replay_v2.metrics import fingerprint


def fixture():
    start=datetime(2022,1,1,tzinfo=timezone.utc); end=start+timedelta(minutes=30)
    rows=[[int((start+timedelta(minutes=i*15)).timestamp()*1000),'100','105','90','101','0',
        int((start+timedelta(minutes=(i+1)*15)).timestamp()*1000)-1] for i in range(2)]
    mark=snapshot(market='mark',symbol='BTCUSDT',interval='15m',source=SOURCES['mark'],
        coverage_start=start.isoformat().replace('+00:00','Z'),coverage_end=end.isoformat().replace('+00:00','Z'),
        fetched_at=end.isoformat().replace('+00:00','Z'),pages=1,raw_rows=rows)
    raw=[dict(symbol='BTCUSDT',fundingTime=int(start.timestamp()*1000)+6,fundingRate='-.001',markPrice=''),
         dict(symbol='BTCUSDT',fundingTime=int((start+timedelta(minutes=15)).timestamp()*1000),fundingRate='.002',markPrice='102')]
    return start,end,mark,raw


def test_derived_mark_preserves_raw_and_existing_settlement_quotes():
    start,end,mark,raw=fixture()
    snap=derive('BTCUSDT',raw,start,end,end,1,mark)
    assert snap.raw_rows==tuple(raw)
    assert [str(p.mark) for p in snap.history.settlements]==['100','102']
    assert snap.quote_lineage[0]['age_ms']==6
    assert snap.quote_lineage[0]['mark_row'][1]=='100'  # Open only, never later close/high/low.
    assert decode(snap.model_dump(mode='json'),mark).history==snap.history
    with pytest.raises(ValueError): FundingSnapshot.model_validate(snap.model_dump(mode='json'))


def test_rejects_future_stale_wrong_or_tampered_mark_source():
    start,end,mark,raw=fixture()
    raw[0]['fundingTime']+=26
    with pytest.raises(ValueError,match='31'):
        derive('BTCUSDT',raw,start,end,end,1,mark)
    raw[0]['fundingTime']-=26
    snap=derive('BTCUSDT',raw,start,end,end,1,mark)
    p=snap.model_dump(mode='json'); p['quote_lineage'][0]['mark_row'][1]='99'
    p['funding_id']=fingerprint({k:v for k,v in p.items() if k!='funding_id'})
    with pytest.raises(ValueError): ReferenceFundingSnapshot.model_validate(p)
    # A valid standalone derivative must also bind the actual native mark series.
    different=mark.model_copy(update={'snapshot_id':'a'*64})
    with pytest.raises(ValueError,match='native mark'):
        decode(snap.model_dump(mode='json'),different)


def test_reference_price_disclosed_in_ledger_and_prepared_audit(monkeypatch):
    import test_donchian_filter_engine as fixtures
    from intraday.replay_v2.donchian_filter_engine import prepare,simulate
    from intraday.replay_v2.donchian_filter_data import decode_bundle
    from test_donchian_filter_study import bundle
    monkeypatch.setattr(fixtures,'START',datetime(2022,1,1,tzinfo=timezone.utc))
    cfg,raw=bundle()
    for s in cfg.weights:
        mark=snapshot(**{k:v for k,v in raw['candles'][s]['mark15m'].items() if k!='snapshot_id'})
        rows=raw['funding'][s]['raw_rows']
        rows[0]['markPrice']=''; rows[0]['fundingTime']+=6; rows[0]['fundingRate']='-.001'
        funding=derive(s,rows,cfg.start,cfg.end,cfg.end,1,mark)
        raw['funding'][s]=funding.model_dump(mode='json')
    raw['bundle_checksum']=fingerprint({k:v for k,v in raw.items() if k!='bundle_checksum'})
    data,funding=decode_bundle(cfg,raw)
    r=simulate(cfg,prepare(cfg,data,funding))
    assert r['summary']['funding_paid_known']>0
    assert r['summary']['funding_price_reference']['BTCUSDT']['quotes']==1
    assert any(e.get('funding_price_source') for e in r['events'])
    assert 'funding_pricing' in r['methodology']
    assert 'missing_funding_settlement_quote_native_mark_open_reference_0_to_31ms' in r['limitations']


def test_explicit_collector_uses_original_rates_and_bound_mark_source(monkeypatch,tmp_path):
    import json
    import test_donchian_filter_engine as fixtures
    from test_donchian_filter_study import bundle
    from intraday.replay_v2 import donchian_oos_data as module
    from intraday.replay_v2.donchian_mark_gap import SparseMarkSnapshot,POLICY,availability
    from intraday.replay_v2.donchian_filter_data import FilterSnapshot,decode_bundle
    from intraday.replay_v2.metrics import encoded
    from intraday.replay_v2.portfolio_study import file_hash
    monkeypatch.setattr(fixtures,'START',datetime(2022,1,1,tzinfo=timezone.utc))
    cfg,raw=bundle(); marks={}; funding={}
    for s in cfg.weights:
        p={k:v for k,v in raw['candles'][s]['mark15m'].items() if k!='snapshot_id'}; p['data_policy']=POLICY
        marks[s]=SparseMarkSnapshot(snapshot_id=fingerprint(p),**p)
        rows=raw['funding'][s]['raw_rows']; rows[0]['markPrice']=''; rows[0]['fundingTime']+=6
        funding[s]=derive(s,rows,cfg.start,cfg.end,cfg.end,1,marks[s])
    mark_audit=tmp_path/'mark-audit.json'; module._write(mark_audit,encoded({'fixture':'mark loader tested separately'}))
    fund_audit=tmp_path/'fund-audit.json'; module._write(fund_audit,encoded({'fixture':'derived loader tested separately'}))
    monkeypatch.setattr(module,'START',cfg.start); monkeypatch.setattr(module,'END',cfg.end)
    monkeypatch.setattr(module,'verify_seal',lambda _:dict(seal_checksum='a'*64,reference_path='old'))
    monkeypatch.setattr(module,'load_mark_gap',lambda *args:(marks,availability([]),{str(mark_audit):file_hash(mark_audit)}))
    monkeypatch.setattr(module,'load_funding_reference',lambda *args:(funding,{str(fund_audit):file_hash(fund_audit)}))
    monkeypatch.setattr(module,'from_futures',lambda s:s)
    def candles(s,i,*args,price_kind=None):
        assert price_kind!='mark'
        return FilterSnapshot.model_validate(raw['candles'][s][('spot' if price_kind is None else 'perp')+i])
    def no_call(*args,**kwargs): raise AssertionError('funding original audit should be reused')
    path=module.collect('seal',tmp_path/'data',approved_mark_gap_audit=mark_audit,approved_funding_reference_audit=fund_audit,
        spot_fetcher=candles,perp_fetcher=candles,funding_fetcher=no_call,progress=lambda _:None)
    saved=json.loads(path.read_text()); _,histories=decode_bundle(cfg,saved)
    assert len(histories['BTCUSDT'].reference_times_ms)==1
    assert saved['funding']['BTCUSDT']['raw_rows'][0]['markPrice']==''
    assert json.loads((path.parent/'qa.json').read_text())['funding_price_reference']['BTCUSDT']['maximum_age_ms']==6
    assert module.collect('seal',path.parent,resume=True,approved_mark_gap_audit=mark_audit,
        approved_funding_reference_audit=fund_audit,progress=lambda _:None)==path
    with pytest.raises(ValueError,match='seal/window'):
        module.collect('seal',path.parent,resume=True,approved_mark_gap_audit=mark_audit,progress=lambda _:None)
