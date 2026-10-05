from datetime import datetime,timedelta,timezone
from decimal import Decimal as D

import pytest

from intraday.replay_v2.donchian_spot_gap import spot_candles, GAP_OPENS, PARTIAL_CLOSES
from intraday.replay_v2.donchian_filter_engine import prepare,simulate
import test_donchian_filter_engine as fixtures


def gap_fixture(monkeypatch):
    start=datetime(2023,3,24,tzinfo=timezone.utc)
    monkeypatch.setattr(fixtures,'START',start)
    cfg,data,funding=fixtures.fixture(days=2)
    for s,tags in data.items():
        raw=[bar.row() for bar in tags['spot15m'] if bar.opened_at not in GAP_OPENS]
        for row in raw:
            if row[0] in PARTIAL_CLOSES[s]: row[6]=PARTIAL_CLOSES[s][row[0]]
        tags['spot15m']=spot_candles(raw,s,cfg.start-timedelta(hours=484),cfg.end)
    return cfg,data,funding


def test_explicit_source_gaps_never_generate_candles(monkeypatch):
    cfg,data,_=gap_fixture(monkeypatch)
    assert len(data['BTCUSDT']['spot15m'])==1936+192-5
    assert not any(b.opened_at in GAP_OPENS for b in data['BTCUSDT']['spot15m'])
    raw=[b.row() for b in data['BTCUSDT']['spot15m']]; raw.pop(0)
    with pytest.raises(ValueError): spot_candles(raw,'BTCUSDT',cfg.start-timedelta(hours=484),cfg.end)


def test_perp_daily_halt_runs_during_spot_gap_and_spot_defers_to_real_open(monkeypatch):
    cfg,data,funding=gap_fixture(monkeypatch)
    trigger=cfg.start+timedelta(hours=12,minutes=45)
    for s,tags in data.items():
        tags['mark15m']=tuple(b.model_copy(update={'open':D(130),'high':D(130),'low':D(130),'close':D(130)})
            if b.opened_at==trigger else b for b in tags['mark15m'])
    r=simulate(cfg,prepare(cfg,data,funding))
    halts=[e for e in r['events'] if e['kind']=='halt' and e['reason']=='daily_loss_limit']
    assert halts and datetime.fromisoformat(halts[0]['at'])>=trigger
    perp=[t for t in r['trades'] if t['market']=='perp' and t['exit_reason']=='daily_loss_limit']
    spot=[t for t in r['trades'] if t['market']=='spot' and t['exit_reason']=='daily_loss_limit']
    assert perp and all(datetime.fromisoformat(t['closed_at'])==trigger for t in perp)
    assert spot and all(datetime.fromisoformat(t['closed_at'])==cfg.start+timedelta(hours=14) for t in spot)
    assert r['summary']['spot_availability']['stale_valuation_samples']>0
    assert all(datetime.fromisoformat(t['closed_at']) not in GAP_OPENS for t in r['trades'] if t['market']=='spot')


def test_perp_stops_not_disabled_by_missing_spot(monkeypatch):
    cfg,data,funding=gap_fixture(monkeypatch)
    at=cfg.start+timedelta(hours=13)
    bars=data['BTCUSDT']['perp15m']
    data['BTCUSDT']['perp15m']=tuple(b.model_copy(update={'high':D(98)}) if b.opened_at==at else b for b in bars)
    r=simulate(cfg,prepare(cfg,data,funding))
    trade=next(t for t in r['trades'] if t['market']=='perp' and t['symbol']=='BTCUSDT')
    assert trade['exit_reason']=='atr_trailing_touch'
    assert datetime.fromisoformat(trade['closed_at'])==at+timedelta(minutes=15,milliseconds=-1)


def test_source_partial_close_is_preserved_and_whitelist_is_strict(monkeypatch):
    cfg,data,_=gap_fixture(monkeypatch)
    rows=data['BTCUSDT']['spot15m']
    partial=next(b for b in rows if int(b.opened_at.timestamp()*1000)==1679661000000)
    assert partial.row()[6]==1679661581646
    raw=[b.row() for b in rows]
    raw[0][6]-=1
    with pytest.raises(ValueError,match='whitelist'):
        spot_candles(raw,'BTCUSDT',cfg.start-timedelta(hours=484),cfg.end)


def test_sparse_snapshot_roundtrip_and_buy_hold_marks_actual_source_only(monkeypatch):
    from intraday.replay_v2.donchian_spot_gap import SparseSpotSnapshot, POLICY
    from intraday.replay_v2.donchian_filter_data import FilterSnapshot, SPOT_SOURCE
    from intraday.replay_v2.donchian_oos_analysis import buy_hold
    from intraday.replay_v2.metrics import fingerprint
    cfg,data,_=gap_fixture(monkeypatch)
    payload=dict(market='spot',symbol='BTCUSDT',interval='15m',source=SPOT_SOURCE,
        coverage_start=(cfg.start-timedelta(hours=484)).isoformat().replace('+00:00','Z'),
        coverage_end=cfg.end.isoformat().replace('+00:00','Z'),
        fetched_at=cfg.end.isoformat().replace('+00:00','Z'),pages=1,
        raw_rows=[b.row() for b in data['BTCUSDT']['spot15m']],data_policy=POLICY)
    snap=SparseSpotSnapshot(snapshot_id=fingerprint(payload),**payload)
    assert SparseSpotSnapshot.model_validate(snap.model_dump(mode='json'))==snap
    with pytest.raises(ValueError): FilterSnapshot.model_validate(snap.model_dump(mode='json'))
    assert buy_hold(cfg,data)['net_pnl']<0


def test_real_funding_during_spot_gap_is_not_skipped(monkeypatch):
    from intraday.replay_v2.contracts import FundingSettlement
    cfg,data,funding=gap_fixture(monkeypatch)
    at=cfg.start+timedelta(hours=13)
    for s,h in funding.items():
        points=sorted((*h.settlements,FundingSettlement(at=at,rate=D('-.001'),mark=90)),key=lambda row:row.at)
        funding[s]=h.model_copy(update={'settlements':tuple(points)})
    r=simulate(cfg,prepare(cfg,data,funding))
    assert r['summary']['funding_paid_known']>0
    assert any(e['kind']=='funding' and e['at']==at.isoformat() for e in r['events'])


def test_gap_collector_is_explicit_and_resume_binds_audit(monkeypatch,tmp_path):
    import json
    from intraday.replay_v2 import donchian_oos_data as module
    from intraday.replay_v2.donchian_spot_gap import SparseSpotSnapshot, POLICY, availability
    from intraday.replay_v2.donchian_filter_data import FilterSnapshot, decode_bundle
    from intraday.replay_v2.metrics import fingerprint,encoded
    from intraday.replay_v2.funding import FundingSnapshot
    from intraday.replay_v2.portfolio_study import file_hash
    from test_donchian_filter_study import bundle
    monkeypatch.setattr(fixtures,'START',datetime(2023,3,24,tzinfo=timezone.utc))
    cfg,raw=bundle()
    sparse={}
    for s,tags in raw['candles'].items():
        p={k:v for k,v in tags['spot15m'].items() if k!='snapshot_id'}
        p['data_policy']=POLICY
        p['raw_rows']=[r for r in p['raw_rows'] if datetime.fromtimestamp(r[0]/1000,timezone.utc) not in GAP_OPENS]
        for r in p['raw_rows']:
            if r[0] in PARTIAL_CLOSES[s]: r[6]=PARTIAL_CLOSES[s][r[0]]
        sparse[s]=SparseSpotSnapshot(snapshot_id=fingerprint(p),**p)
    audit=tmp_path/'audit.json'; module._write(audit,encoded({'fixture':'loader tested separately'}))
    monkeypatch.setattr(module,'START',cfg.start); monkeypatch.setattr(module,'END',cfg.end)
    monkeypatch.setattr(module,'verify_seal',lambda _:dict(seal_checksum='a'*64,reference_path='old'))
    monkeypatch.setattr(module,'load_spot_gap',lambda *args:(sparse,availability([]),{str(audit):file_hash(audit)}))
    monkeypatch.setattr(module,'from_futures',lambda s:s)
    def candles(s,i,*args,price_kind=None):
        assert not (i=='15m' and price_kind is None)
        market='spot' if price_kind is None else 'perp' if price_kind=='trade' else 'mark'
        return FilterSnapshot.model_validate(raw['candles'][s][market+i])
    path=module.collect('seal',tmp_path/'data',approved_spot_gap_audit=audit,spot_fetcher=candles,
        perp_fetcher=candles,funding_fetcher=lambda s,*args:FundingSnapshot.model_validate(raw['funding'][s]),progress=lambda _:None)
    saved=json.loads(path.read_text()); data,funding=decode_bundle(cfg,saved)
    assert simulate(cfg,prepare(cfg,data,funding))['summary']['spot_availability']['synthetic_bars']==0
    assert str(path.parent/'spot-availability.json') in saved['source_files_sha256']
    assert module.collect('seal',path.parent,resume=True,approved_spot_gap_audit=audit,progress=lambda _:None)==path
    with pytest.raises(ValueError,match='seal/window'):
        module.collect('seal',path.parent,resume=True,progress=lambda _:None)
