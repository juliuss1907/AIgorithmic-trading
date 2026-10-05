import json
from decimal import Decimal as D

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.metrics import encoded, fingerprint


def test_five_cases_restore_adx_config_and_verify_full_journals(tmp_path):
    from test_donchian_filter_study import bundle
    from intraday.replay_v2.donchian_adx_study import run_study
    cfg,raw=bundle()
    for tags in raw['candles'].values():
        for tag in ('spot4h','perp4h'):
            payload=tags[tag]
            payload['raw_rows'][599][5]='300'
            payload['snapshot_id']=fingerprint({k:v for k,v in payload.items() if k!='snapshot_id'})
    raw['bundle_checksum']=fingerprint({k:v for k,v in raw.items() if k!='bundle_checksum'})
    path=tmp_path/'inputs.json';_write(path,encoded(raw))
    result=run_study(path,tmp_path/'runs',progress=lambda _:None)
    assert len(result['results'])==5
    assert len({r['dataset_checksum'] for r in result['results']})==1
    assert all(r['deterministic_rerun_verified'] for r in result['results'])
    # The synthetic breakout has weak ADX: off must enter while ADX25 rejects it.
    assert result['results'][0]['summary']['closed_trades']>0
    assert result['results'][-1]['summary']['closed_trades']==0
    assert [r['config']['adx_threshold'] for r in result['results']]==[None,20,18,15,25]
    assert not result['activation_allowed'] and not result['official_gate_eligible']
    assert result['research_on_seen_data']
    assert (tmp_path/'runs'/'comparison.md').exists()
    resumed=run_study(path,tmp_path/'runs',resume=True,progress=lambda _:None)
    assert resumed==result


def test_calendar_periods_use_equal_halves_and_label_partial_window():
    from datetime import datetime, timezone
    from intraday.replay_v2.donchian_adx_study import calendar_periods
    start=datetime(2022,1,1,tzinfo=timezone.utc);end=datetime(2024,10,2,tzinfo=timezone.utc)
    curve=[dict(at=t,equity_known=str(v)) for t,v in [
        ('2022-06-30T23:59:59+00:00',1100),('2022-12-31T23:59:59+00:00',1050),
        ('2023-06-30T23:59:59+00:00',1200),('2023-12-31T23:59:59+00:00',1150),
        ('2024-06-30T23:59:59+00:00',1250),('2024-10-02T00:00:00+00:00',1300)]]
    rows=calendar_periods(curve,start,end,D(1000))
    assert len(rows)==6
    assert sum(D(str(r['pnl'])) for r in rows)==300
    assert all(r['full_calendar_half'] for r in rows[:-1])
    assert not rows[-1]['full_calendar_half']
