import json
from decimal import Decimal as D

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.metrics import encoded, fingerprint


def test_five_cases_restore_adx_config_and_verify_full_journals(tmp_path, monkeypatch):
    from test_donchian_filter_study import bundle
    from intraday.replay_v2.donchian_adx_study import run_study
    from intraday.replay_v2 import donchian_adx_study as study
    from intraday.replay_v2.donchian_spot_gap import availability
    original=study.simulate
    def with_source_metadata(*args, **kwargs):
        report=original(*args, **kwargs)
        report['summary']['spot_availability']=availability([])
        return report
    monkeypatch.setattr(study,'simulate',with_source_metadata)
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
    from intraday.replay_v2.donchian_adx_analysis import describe
    analysis=describe(tmp_path/'runs'/'comparison.json',tmp_path/'analysis')
    assert len(analysis['results'])==5
    assert analysis['results'][0]['rejected_off_trades']['closed_trades']==0
    assert not analysis['activation_allowed']
    assert (tmp_path/'analysis'/'report.md').exists()
    changed=json.loads(path.read_text());changed['window']['end']='2026-10-03T00:00:00+00:00'
    other=tmp_path/'other.json';_write(other,encoded(changed))
    import pytest
    with pytest.raises(ValueError,match='binding changed'):
        run_study(other,tmp_path/'runs',resume=True,progress=lambda _:None)


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


def test_perp_leverage_universe_stress_cases_and_later_start(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from intraday.replay_v2.donchian_adx_config import cases
    from intraday.replay_v2.donchian_adx_study import run_study
    from intraday.replay_v2 import donchian_five_data
    from intraday.replay_v2.donchian_recent import collect, symbol_groups
    from test_donchian_prospective import fetchers
    start = datetime(2026, 10, 11, tzinfo=timezone.utc)
    end = start+timedelta(days=2)
    variants = cases(start, end, universe='perp-leverage')
    assert [n for n, _ in variants] == ['ETH-NEAR-SOL-H4', 'ETH-NEAR-SOL-H4-cost2x',
                                       'PERP1X', 'PERP1X-cost2x', 'PERP5X', 'PERP5X-cost2x']
    path = collect(symbol_groups(start, end)[-1], start, end, tmp_path/'inputs',
                   now=end+timedelta(hours=1), progress=lambda _: None, **fetchers())
    monkeypatch.setattr(donchian_five_data, 'read_manifest', lambda p: json.loads(p.read_text()))
    result = run_study(path, tmp_path/'study', universe='perp-leverage',
                       window_start=(start+timedelta(days=1)).isoformat(), progress=lambda _: None)
    assert result['preset'] == 'donchian-perp-leverage' and len(result['results']) == 6
    assert all(r['deterministic_rerun_verified'] for r in result['results'])
    assert len({r['dataset_checksum'] for r in result['results']}) == 1
    assert result['window']['start'] == (start+timedelta(days=1)).isoformat()
    assert not result['activation_allowed']
    assert run_study(path, tmp_path/'study', universe='perp-leverage', resume=True,
                     window_start=(start+timedelta(days=1)).isoformat(), progress=lambda _: None) == result
