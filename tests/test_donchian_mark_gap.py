from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

import test_donchian_filter_engine as fixtures
from intraday.replay_v2.donchian_mark_gap import mark_candles, GAP_OPEN
from intraday.replay_v2.donchian_filter_engine import prepare, simulate


def gap_fixture(monkeypatch):
    monkeypatch.setattr(fixtures,'START',datetime(2023,11,10,tzinfo=timezone.utc))
    cfg,data,funding=fixtures.fixture()
    for s,tags in data.items():
        rows=[b.row() for b in tags['mark15m'] if b.opened_at!=GAP_OPEN]
        tags['mark15m']=mark_candles(rows,s,cfg.start,cfg.end)
    return cfg,data,funding


def test_only_the_approved_mark_bar_is_missing(monkeypatch):
    cfg,data,_=gap_fixture(monkeypatch)
    rows=data['BTCUSDT']['mark15m']
    assert len(rows)==95 and not any(b.opened_at==GAP_OPEN for b in rows)
    with pytest.raises(ValueError): mark_candles([b.row() for b in rows[1:]],'BTCUSDT',cfg.start,cfg.end)


def test_perp_stop_runs_with_stale_mark_and_has_true_trade_price(monkeypatch):
    cfg,data,funding=gap_fixture(monkeypatch)
    tags=data['BTCUSDT']
    tags['perp15m']=tuple(b.model_copy(update={'high':D(98)}) if b.opened_at==GAP_OPEN else b for b in tags['perp15m'])
    r=simulate(cfg,prepare(cfg,data,funding))
    trade=next(t for t in r['trades'] if t['market']=='perp' and t['symbol']=='BTCUSDT')
    assert trade['exit_reason']=='atr_trailing_touch'
    assert datetime.fromisoformat(trade['closed_at'])==GAP_OPEN+timedelta(minutes=15,milliseconds=-1)
    assert r['summary']['mark_availability']['synthetic_bars']==0
    stale=[p for p in r['equity_curve'] if p.get('stale_mark_symbols')]
    assert stale and all(GAP_OPEN<=datetime.fromisoformat(p['at'])<GAP_OPEN+timedelta(minutes=15) for p in stale)
    assert all(datetime.fromisoformat(p['mark_observed_at']['BTCUSDT'])<GAP_OPEN for p in stale)
    assert not next(p for p in r['equity_curve'] if p['at']==(GAP_OPEN+timedelta(minutes=15)).isoformat())['stale_mark_symbols']


def test_new_actual_mark_replaces_stale_at_reopening(monkeypatch):
    cfg,data,funding=gap_fixture(monkeypatch)
    at=GAP_OPEN+timedelta(minutes=15)
    for s,tags in data.items():
        tags['mark15m']=tuple(b.model_copy(update={'open':D(130),'high':D(130),'low':D(130),'close':D(130)})
            if b.opened_at==at else b for b in tags['mark15m'])
    r=simulate(cfg,prepare(cfg,data,funding))
    exits=[t for t in r['trades'] if t['exit_reason']=='daily_loss_limit']
    assert exits and all(datetime.fromisoformat(t['closed_at'])==at for t in exits)


def test_mark_snapshot_and_collection_keep_immutable_lineage(monkeypatch,tmp_path):
    import json
    from intraday.replay_v2 import donchian_oos_data as module
    from intraday.replay_v2.donchian_mark_gap import SparseMarkSnapshot,POLICY,availability
    from intraday.replay_v2.donchian_filter_data import FilterSnapshot,decode_bundle
    from intraday.replay_v2.funding import FundingSnapshot
    from intraday.replay_v2.metrics import fingerprint,encoded
    from intraday.replay_v2.portfolio_study import file_hash
    from test_donchian_filter_study import bundle
    monkeypatch.setattr(fixtures,'START',datetime(2023,11,10,tzinfo=timezone.utc))
    cfg,raw=bundle(); sparse={}
    for s,tags in raw['candles'].items():
        payload={k:v for k,v in tags['mark15m'].items() if k!='snapshot_id'}
        payload['data_policy']=POLICY
        payload['raw_rows']=[r for r in payload['raw_rows'] if r[0]!=int(GAP_OPEN.timestamp()*1000)]
        sparse[s]=SparseMarkSnapshot(snapshot_id=fingerprint(payload),**payload)
        assert SparseMarkSnapshot.model_validate(sparse[s].model_dump(mode='json'))==sparse[s]
        with pytest.raises(ValueError): FilterSnapshot.model_validate(sparse[s].model_dump(mode='json'))
    audit=tmp_path/'audit.json'; module._write(audit,encoded({'fixture':'loader tested separately'}))
    cache=tmp_path/'cache'; cache.mkdir()
    module._write(cache/'BTCUSDT-perp15m.json',encoded(raw['candles']['BTCUSDT']['perp15m']))
    monkeypatch.setattr(module,'START',cfg.start); monkeypatch.setattr(module,'END',cfg.end)
    monkeypatch.setattr(module,'verify_seal',lambda _:dict(seal_checksum='a'*64,reference_path='old'))
    monkeypatch.setattr(module,'load_mark_gap',lambda *args:(sparse,availability([]),{str(audit):file_hash(audit)}))
    monkeypatch.setattr(module,'from_futures',lambda s:s)
    def candles(s,i,*args,price_kind=None):
        assert price_kind!='mark'
        assert not (s=='BTCUSDT' and i=='15m' and price_kind=='trade')
        return FilterSnapshot.model_validate(raw['candles'][s][('spot' if price_kind is None else 'perp')+i])
    path=module.collect('seal',tmp_path/'data',approved_mark_gap_audit=audit,reuse_root=cache,
        spot_fetcher=candles,perp_fetcher=candles,
        funding_fetcher=lambda s,*args:FundingSnapshot.model_validate(raw['funding'][s]),progress=lambda _:None)
    saved=json.loads(path.read_text()); data,funding=decode_bundle(cfg,saved)
    parent=saved['reuse_lineage']['BTCUSDT']['perp15m']
    assert parent['active_rows_reused'] and parent['parent_snapshot_id']==raw['candles']['BTCUSDT']['perp15m']['snapshot_id']
    assert simulate(cfg,prepare(cfg,data,funding))['summary']['mark_availability']['synthetic_bars']==0
    assert str(cache/'BTCUSDT-perp15m.json') in saved['source_files_sha256']
    assert module.collect('seal',path.parent,resume=True,approved_mark_gap_audit=audit,reuse_root=cache,progress=lambda _:None)==path
    with pytest.raises(ValueError,match='seal/window'):
        module.collect('seal',path.parent,resume=True,reuse_root=cache,progress=lambda _:None)
