from copy import deepcopy
from decimal import Decimal as D

import pytest

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_adx_config import ADXStudyConfig, FiveCoinADXConfig, cases, START, END
from intraday.replay_v2.donchian_five_data import SCHEMA, reference, read_manifest
from intraday.replay_v2.metrics import encoded, fingerprint


def five_bundle(tmp_path):
    from test_donchian_filter_study import bundle
    cfg,raw=bundle()
    base=tmp_path/'base.json'; _write(base,encoded(raw))
    manifest=dict(schema_version=SCHEMA,base=reference(base),window=raw['window'],additions={},source_files_sha256={})
    for symbol in ('NEARUSDT','ZECUSDT'):
        refs={}
        for tag,payload in {**raw['candles']['BTCUSDT'],'funding':raw['funding']['BTCUSDT']}.items():
            payload=deepcopy(payload)
            key='funding_id' if tag=='funding' else 'snapshot_id'
            if tag=='funding':
                payload['history']['symbol']=symbol
                for r in payload['raw_rows']: r['symbol']=symbol
            else: payload['symbol']=symbol
            payload[key]=fingerprint({k:v for k,v in payload.items() if k!=key})
            path=tmp_path/f'{symbol}-{tag}.json'; _write(path,encoded(payload))
            refs[tag]=reference(path,payload[key])
        manifest['additions'][symbol]=refs
    manifest['manifest_checksum']=fingerprint(manifest)
    path=tmp_path/'inputs.json'; _write(path,encoded(manifest))
    return cfg,path


def test_locked_weights_roundtrip_and_legacy_hash():
    old=cases()
    assert all(type(c) is ADXStudyConfig and 'universe' not in c.model_dump() for _,c in old)
    assert fingerprint(old[0][1].model_dump(mode='json'))=='d601d9ba4f1c5f4f3de5583c89eb16b40d97f158d1125452a705048f7baa9205'
    new=cases(universe='five')
    assert [c.adx_threshold for _,c in new]==[None,20,18,15,25]
    for _,cfg in new:
        assert cfg.weights==cfg.perp_weights==dict(BTCUSDT=D('.4'),ETHUSDT=D('.2'),SOLUSDT=D('.2'),NEARUSDT=D('.1'),ZECUSDT=D('.1'))
        assert cfg==FiveCoinADXConfig.model_validate_json(cfg.model_dump_json())
    with pytest.raises(ValueError):
        FiveCoinADXConfig(start=START,end=END,weights=old[0][1].weights)
    with pytest.raises(ValueError): cases(universe='six')


def test_manifest_five_case_replay_resume_and_analysis(tmp_path):
    from intraday.replay_v2.donchian_adx_study import run_study
    from intraday.replay_v2.donchian_adx_analysis import describe
    _,path=five_bundle(tmp_path)
    result=run_study(path,tmp_path/'runs',universe='five',progress=lambda _:None)
    assert len(result['results'])==5
    for r in result['results']:
        assert r['config']['symbol']=='BTC+ETH+NEAR+SOL+ZEC'
        assert len(r['summary']['contributions'])==10
        assert r['deterministic_rerun_verified']
    assert result==run_study(path,tmp_path/'runs',universe='five',resume=True,progress=lambda _:None)
    run_study(tmp_path/'base.json',tmp_path/'three',progress=lambda _:None)
    analysis=describe(tmp_path/'runs/comparison.json',tmp_path/'analysis',baseline_path=tmp_path/'three/comparison.json')
    assert all(len(r['coin_market'])==10 for r in analysis['results'])
    assert len(analysis['three_coin_comparison']['results'])==5
    assert len(analysis['new_coin_contributions'])==5
    from intraday.replay_v2.donchian_adx_study import verify_three_coin
    checked=verify_three_coin(tmp_path/'three/comparison.json',tmp_path/'legacy',progress=lambda _:None)
    assert len(checked)==5 and all(r['matched'] for r in checked)
    with pytest.raises(ValueError):
        run_study(path,tmp_path/'runs',resume=True,progress=lambda _:None)


def test_manifest_hash_tamper_is_rejected(tmp_path):
    _,path=five_bundle(tmp_path)
    target=tmp_path/'NEARUSDT-spot4h.json'
    target.write_text(target.read_text()+' ')
    with pytest.raises(ValueError,match='checksum changed'): read_manifest(path)


def test_new_coin_cannot_inherit_old_gap_policy():
    from intraday.replay_v2.donchian_spot_gap import spot_candles
    from intraday.replay_v2.donchian_mark_gap import mark_candles
    for symbol in ('NEARUSDT','ZECUSDT'):
        for reader in (spot_candles,mark_candles):
            with pytest.raises(ValueError,match='identity/coverage'): reader([],symbol,START,END)


def test_mixed_sparse_and_dense_series_and_missing_new_coin_bar():
    from test_donchian_filter_engine import fixture
    from intraday.replay_v2.donchian_filter_engine import prepare,simulate
    from intraday.replay_v2.donchian_spot_gap import SourceSpotCandle
    from intraday.replay_v2.donchian_mark_gap import SourceMarkCandle
    cfg,data,funding=fixture()
    for s in ('NEARUSDT','ZECUSDT'):
        data[s]=deepcopy(data['BTCUSDT'])
        funding[s]=funding['BTCUSDT'].model_copy(update={'symbol':s})
    cfg=FiveCoinADXConfig(start=cfg.start,end=cfg.end,adx_threshold=None)
    for tag,model in [('spot15m',SourceSpotCandle),('mark15m',SourceMarkCandle)]:
        data['BTCUSDT'][tag]=tuple(model.model_validate(b.model_dump()) for b in data['BTCUSDT'][tag])
    p=prepare(cfg,data,funding)
    assert p.spot_gap_policy==p.mark_gap_policy=={'BTCUSDT'}
    assert simulate(cfg,p)['status']=='complete'
    data['NEARUSDT']['spot15m']=data['NEARUSDT']['spot15m'][1:]
    with pytest.raises(ValueError,match='gap or duplicate'): prepare(cfg,data,funding)


def test_strict_collector_keeps_rejected_source_without_repair(tmp_path,monkeypatch):
    from intraday.replay_v2 import donchian_five_data as data
    at=int((START-data.WARMUP['4h']).timestamp()*1000)
    bad=[at,'10','11','9','10','1',at+123]
    monkeypatch.setattr(data,'public_spot_json',lambda _: [bad])
    with pytest.raises(ValueError,match='evidence'): data.collect_series('NEARUSDT','spot4h',tmp_path)
    import json
    rejected=json.loads(next(tmp_path.glob('*rejected*')).read_text())
    assert rejected['raw_rows']==[bad]
    assert not (tmp_path/'NEARUSDT-spot4h.json').exists()


def test_full_raw_audit_preserves_metadata_and_flags_missing_coverage(tmp_path,monkeypatch):
    from intraday.replay_v2 import donchian_five_data as data
    at=int((START-data.WARMUP['4h']).timestamp()*1000)
    bad=[at,'10','11','9','10','1',at+123]
    pages=iter([[bad],[]])
    monkeypatch.setattr(data,'public_spot_json',lambda _:next(pages))
    monkeypatch.setattr(data.time,'sleep',lambda _:None)
    raw=data.audit_raw_series('NEARUSDT','spot4h',tmp_path)
    assert raw['raw_rows']==[bad]
    assert raw['missing_opens_ms'][0]==at+14400000
    assert raw['issues']['noncanonical_close_times'][0]['close_ms']==at+123
    assert raw['accepted_snapshot'] is False
    assert data.audit_raw_series('NEARUSDT','spot4h',tmp_path)==raw


@pytest.mark.parametrize('symbol',['NEARUSDT','ZECUSDT'])
def test_new_coin_cannot_inherit_old_price_whitelist(symbol):
    from datetime import timedelta
    from intraday.replay_v2.contracts import Candle
    from intraday.replay_v2.intraday_timeframes import IntradayCandle
    from intraday.replay_v2.donchian_adx_boundaries import JOIN, OPENS, verify_join_boundaries
    a,b=OPENS['BTCUSDT']
    large=[Candle(opened_at=JOIN,available_at=JOIN+timedelta(hours=4),open=a,close=100,high=100000,low=1,volume=1)]
    small=[IntradayCandle(interval='15m',opened_at=JOIN+timedelta(minutes=15*i),
                         available_at=JOIN+timedelta(minutes=15*(i+1)),open=b,close=100,high=100000,low=1,volume=1) for i in range(16)]
    with pytest.raises(ValueError): verify_join_boundaries(symbol,large,small,JOIN,JOIN+timedelta(hours=4),'new coin')


@pytest.mark.parametrize('age',[0,31,32])
def test_new_funding_references_require_real_causal_open(age):
    from datetime import timedelta
    from test_donchian_funding_reference import fixture
    from intraday.replay_v2.donchian_funding_reference import derive,decode
    from intraday.replay_v2.donchian_filter_data import snapshot
    start,end,mark,rows=fixture()
    shift=timedelta(days=1461)  # 2026: new permission does not extend legacy coins.
    payload=mark.model_dump(mode='json',exclude={'snapshot_id'})
    payload.update(symbol='NEARUSDT',coverage_start=(start+shift).isoformat().replace('+00:00','Z'),
                   coverage_end=(end+shift).isoformat().replace('+00:00','Z'),fetched_at=(end+shift).isoformat().replace('+00:00','Z'))
    for row in payload['raw_rows']:
        row[0]+=int(shift.total_seconds()*1000); row[6]+=int(shift.total_seconds()*1000)
    mark=snapshot(**payload)
    raw=[dict(symbol='NEARUSDT',fundingTime=int((start+shift).timestamp()*1000)+age,fundingRate='-.001',markPrice='')]
    if age>31:
        with pytest.raises(ValueError,match='31ms'): derive('NEARUSDT',raw,start+shift,end+shift,end+shift,1,mark)
    else:
        snap=derive('NEARUSDT',raw,start+shift,end+shift,end+shift,1,mark)
        assert snap.raw_rows==tuple(raw)
        assert snap.quote_lineage[0]['age_ms']==age
        assert decode(snap.model_dump(mode='json'),mark)==snap
