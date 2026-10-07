from datetime import datetime,timedelta,timezone
from decimal import Decimal as D

import pytest

from intraday.replay_v2 import donchian_five_policy as policy
from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.intraday_timeframes import IntradayCandle
from intraday.replay_v2.metrics import fingerprint


def row(at,close=None):
    return [int(at.timestamp()*1000),'10','11','9','10','1',close or int((at+timedelta(minutes=15)).timestamp()*1000)-1]


@pytest.mark.parametrize('symbol',['NEARUSDT','ZECUSDT'])
def test_exact_spot_gap_and_partial_close_preserve_observed_availability(symbol):
    start=datetime(2023,3,24,12,tzinfo=timezone.utc);end=start+timedelta(hours=4)
    rows=[row(start+timedelta(minutes=15*i)) for i in range(16) if i not in range(3,8)]
    rows[2][6]=policy.PARTIAL_CLOSES[symbol][rows[2][0]]
    candles=policy.spot_candles(rows,symbol,start,end)
    assert len(candles)==11
    assert candles[2].row()==rows[2]
    assert policy.stale_symbols(candles[2].available_at,{symbol})==[symbol]
    assert policy.stale_symbols(start+timedelta(hours=2),{symbol})==[]
    rows[2][6]+=1
    with pytest.raises(ValueError,match='metadata'): policy.spot_candles(rows,symbol,start,end)
    rows[2][6]-=1;rows.pop()
    with pytest.raises(ValueError,match='gap/duplicate'): policy.spot_candles(rows,symbol,start,end)


def test_new_warmup_interval_view_leaves_raw_evidence_intact():
    start=datetime.fromtimestamp(policy.WARMUP_OPEN/1000,timezone.utc);end=start+timedelta(hours=4)
    raw=[[policy.WARMUP_OPEN,'10','11','9','10','1',policy.WARMUP_CLOSE]]
    payload=dict(market='spot',symbol='NEARUSDT',interval='4h',source='https://api.binance.com/api/v3/klines',
                 coverage_start=start.isoformat().replace('+00:00','Z'),coverage_end=end.isoformat().replace('+00:00','Z'),
                 fetched_at=end.isoformat().replace('+00:00','Z'),pages=1,raw_rows=raw,
                 data_policy=policy.POLICY,original_source_sha256='a'*64)
    snap=policy.ApprovedFiveSnapshot(snapshot_id=fingerprint(payload),**payload)
    assert snap.raw_rows[0][6]==policy.WARMUP_CLOSE
    assert snap.candles()[0].row()[6]==policy.CANONICAL_CLOSE
    payload['raw_rows'][0][6]+=1
    with pytest.raises(ValueError,match='exact approved'): policy.ApprovedFiveSnapshot(snapshot_id=fingerprint(payload),**payload)


@pytest.mark.parametrize('symbol',['NEARUSDT','ZECUSDT'])
def test_mark_gap_permits_only_the_single_original_missing_open(symbol):
    start=policy.GAP_OPEN-timedelta(minutes=15);end=policy.GAP_OPEN+timedelta(minutes=30)
    rows=[row(start),row(policy.GAP_OPEN+timedelta(minutes=15))]
    assert len(policy.mark_candles(rows,symbol,start,end))==2
    with pytest.raises(ValueError,match='gap/duplicate'): policy.mark_candles(rows[:-1],symbol,start,end)
    rows[0][6]-=1
    with pytest.raises(ValueError,match='metadata'): policy.mark_candles(rows,symbol,start,end)


@pytest.mark.parametrize('key,pair',list(policy.PRICE_DIFFERENCES.items()))
def test_only_exact_approved_native_price_tuples_are_admitted(key,pair):
    market,symbol,at=key;start=datetime.fromisoformat(at);end=start+timedelta(hours=4)
    op,cp=pair;opening=tuple(map(D,op)) if op else (D(10),D(10));closing=tuple(map(D,cp)) if cp else (D(10),D(10))
    large=[Candle(opened_at=start,available_at=end,open=opening[0],close=closing[0],high=100,low=1,volume=1)]
    small=[IntradayCandle(interval='15m',opened_at=start+timedelta(minutes=15*i),
             available_at=start+timedelta(minutes=15*(i+1)),open=opening[1],close=closing[1],high=100,low=1,volume=1) for i in range(16)]
    policy.verify_five_boundaries(symbol,large,small,start,end,market+' H4/M15')
    changed=[large[0].model_copy(update={'close':closing[0]+D('.0001')})]
    with pytest.raises(ValueError): policy.verify_five_boundaries(symbol,changed,small,start,end,market+' H4/M15')


def test_real_policy_types_mix_with_old_and_dense_series_without_broadening_whitelists():
    from test_donchian_filter_engine import fixture
    from copy import deepcopy
    from intraday.replay_v2.donchian_adx_config import FiveCoinADXConfig
    from intraday.replay_v2.donchian_filter_engine import prepare,simulate
    from intraday.replay_v2.donchian_spot_gap import SourceSpotCandle,spot_candles
    cfg,data,funding=fixture()
    data['BTCUSDT']['spot15m']=tuple(SourceSpotCandle.model_validate(b.model_dump()) for b in data['BTCUSDT']['spot15m'])
    for s in ('NEARUSDT','ZECUSDT'):
        data[s]=deepcopy(data['ETHUSDT'])
        funding[s]=funding['ETHUSDT'].model_copy(update={'symbol':s})
        data[s]['spot15m']=tuple(policy.FiveSpotCandle.model_validate(b.model_dump()) for b in data[s]['spot15m'])
        data[s]['mark15m']=tuple(policy.FiveMarkCandle.model_validate(b.model_dump()) for b in data[s]['mark15m'])
        with pytest.raises(ValueError):spot_candles([b.row() for b in data[s]['spot15m']],s,data[s]['spot15m'][0].opened_at,cfg.end)
    cfg=FiveCoinADXConfig(start=cfg.start,end=cfg.end,adx_threshold=None)
    prepared=prepare(cfg,data,funding,native_boundary_policy=policy.POLICY)
    assert prepared.five_source_symbols=={'NEARUSDT','ZECUSDT'}
    report=simulate(cfg,prepared)
    assert report['status']=='complete' and len(report['summary']['contributions'])==10
    assert report['summary']['new_coin_data_policy']['policy']==policy.POLICY
