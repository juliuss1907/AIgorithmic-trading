from datetime import timedelta
from decimal import Decimal as D

import pytest

from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.intraday_timeframes import IntradayCandle
from intraday.replay_v2.intraday_data import verify_boundaries
from intraday.replay_v2.donchian_native_boundaries import OPEN,CLOSES,verify_perp_boundaries
from intraday.replay_v2.donchian_spot_gap import BOUNDARY_OPEN,BOUNDARY_PRICES,verify_spot_boundaries


def bars(at,opened,closed,small_open,small_close):
    large=[Candle(opened_at=at,available_at=at+timedelta(hours=4),open=opened,close=closed,high=40000,low=1,volume=1)]
    small=[IntradayCandle(interval='15m',opened_at=at+timedelta(minutes=15*i),
        available_at=at+timedelta(minutes=15*(i+1)),open=small_open,close=small_close,high=40000,low=1,volume=1) for i in range(16)]
    return large,small


@pytest.mark.parametrize('symbol',sorted(CLOSES))
def test_perp_accepts_only_exact_whitelisted_close_pair(symbol):
    a,b=CLOSES[symbol]; large,small=bars(OPEN,D(100),a,D(100),b)
    with pytest.raises(ValueError): verify_boundaries(symbol,large,small,OPEN,OPEN+timedelta(hours=4),'strict')
    verify_perp_boundaries(symbol,large,small,OPEN,OPEN+timedelta(hours=4),'approved')
    mutated=[c.model_copy(update={'close':c.close+D('.01')}) for c in large]
    with pytest.raises(ValueError): verify_perp_boundaries(symbol,mutated,small,OPEN,OPEN+timedelta(hours=4),'approved')
    shifted=[c.model_copy(update={'opened_at':c.opened_at+timedelta(days=1),'available_at':c.available_at+timedelta(days=1)}) for c in large]
    with pytest.raises(ValueError): verify_perp_boundaries(symbol,shifted,small,OPEN,OPEN+timedelta(days=2),'approved')


@pytest.mark.parametrize('symbol',sorted(BOUNDARY_PRICES))
def test_spot_accepts_only_exact_whitelisted_open_pair(symbol):
    a,b=BOUNDARY_PRICES[symbol]; large,small=bars(BOUNDARY_OPEN,a,D(100),b,D(100))
    verify_spot_boundaries(symbol,large,small,BOUNDARY_OPEN,BOUNDARY_OPEN+timedelta(hours=4),'approved')
    with pytest.raises(ValueError):
        verify_spot_boundaries(symbol,[large[0].model_copy(update={'close':D(101)})],small,
            BOUNDARY_OPEN,BOUNDARY_OPEN+timedelta(hours=4),'approved')


def test_perp_bundle_requires_explicit_policy_and_engine_binds_it(monkeypatch):
    from datetime import datetime,timezone
    import test_donchian_filter_engine as fixtures
    from test_donchian_filter_study import bundle
    from intraday.replay_v2.donchian_filter_data import decode_bundle
    from intraday.replay_v2.donchian_filter_engine import prepare,simulate
    from intraday.replay_v2.donchian_native_boundaries import POLICY
    from intraday.replay_v2.metrics import fingerprint
    monkeypatch.setattr(fixtures,'START',datetime(2023,11,10,tzinfo=timezone.utc))
    cfg,raw=bundle(); tags=raw['candles']['BTCUSDT']
    for tag,at,value in [('perp4h',OPEN,'37118.40'),('perp15m',OPEN+timedelta(hours=3,minutes=45),'37092.60')]:
        p=tags[tag]
        row=next(r for r in p['raw_rows'] if r[0]==int(at.timestamp()*1000))
        row[2]='40000'; row[4]=value
        p['snapshot_id']=fingerprint({k:v for k,v in p.items() if k!='snapshot_id'})
    raw['bundle_checksum']=fingerprint({k:v for k,v in raw.items() if k!='bundle_checksum'})
    with pytest.raises(ValueError,match='boundary'): decode_bundle(cfg,raw)
    raw['native_boundary_policy']=POLICY
    raw['bundle_checksum']=fingerprint({k:v for k,v in raw.items() if k!='bundle_checksum'})
    data,funding=decode_bundle(cfg,raw)
    with pytest.raises(ValueError,match='boundary'): prepare(cfg,data,funding)
    r=simulate(cfg,prepare(cfg,data,funding,native_boundary_policy=POLICY))
    assert not r['summary']['native_boundary_disclosure']['generic_price_tolerance']
    assert 'exact_whitelisted_native_h4_m15_price_disagreements' in r['limitations']
