from datetime import timedelta

import pytest

from test_donchian_filter_engine import fixture
from intraday.replay_v2.artifacts import _write, read_report
from intraday.replay_v2.donchian_filter_data import snapshot, SOURCES, decode_bundle
from intraday.replay_v2.donchian_filter_study import run_study, study_configs
from intraday.replay_v2.funding import FundingSnapshot, SOURCE
from intraday.replay_v2.metrics import encoded, fingerprint


def bundle():
    cfg,data,funding = fixture()
    raw = dict(schema_version='donchian-filters-1',window={'start':cfg.start.isoformat(),'end':cfg.end.isoformat()},
               candles={},funding={},reuse_lineage={s:{} for s in cfg.weights},source_files_sha256={})
    for s,series in data.items():
        raw['candles'][s] = {}
        for tag,rows in series.items():
            market = 'spot' if tag.startswith('spot') else 'mark' if tag=='mark15m' else 'perp'
            snap = snapshot(market=market,symbol=s,interval='4h' if tag.endswith('4h') else '15m',
                source=SOURCES[market],coverage_start=rows[0].opened_at.isoformat().replace('+00:00','Z'),
                coverage_end=cfg.end.isoformat().replace('+00:00','Z'),fetched_at=(cfg.end+timedelta(days=1)).isoformat().replace('+00:00','Z'),
                pages=1,raw_rows=[b.row() for b in rows])
            raw['candles'][s][tag] = snap.model_dump(mode='json')
        history = funding[s].model_copy(update={'source':SOURCE})
        payload = dict(schema_version='1',history=history.model_dump(mode='json'),
            fetched_at=(cfg.end+timedelta(days=1)).isoformat().replace('+00:00','Z'),pages=1,
            raw_rows=[dict(symbol=s,fundingTime=int(row.at.timestamp()*1000),fundingRate='0',markPrice='90')
                      for row in history.settlements])
        raw['funding'][s] = FundingSnapshot(funding_id=fingerprint(payload),**payload).model_dump(mode='json')
    return cfg,{**raw,'bundle_checksum':fingerprint(raw)}


def test_decode_full_native_bundle_and_twenty_distinct_case_ids(tmp_path):
    cfg,raw = bundle()
    decoded,funding = decode_bundle(cfg,raw)
    assert set(decoded) == set(funding) == set(cfg.weights)
    assert len(study_configs(cfg.start,cfg.end)) == 20
    path = tmp_path/'inputs.json'
    _write(path,encoded(raw))
    receipt = run_study(path,tmp_path/'results',progress=lambda x:None)
    assert len({r['result_id'] for r in receipt['results']}) == 20
    assert len({r['dataset_checksum'] for r in receipt['results']}) == 1
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert not receipt['activation_allowed'] and not receipt['official_gate_eligible']
    assert 'UTC+7' in (tmp_path/'results'/'comparison.md').read_text()
    for row in receipt['results']:
        public = read_report(tmp_path/'results'/'reports',row['run_id'])
        assert public['summary'] == row['summary']
    with pytest.raises(FileExistsError):
        run_study(path,tmp_path/'results',progress=lambda x:None)


def test_missing_source_hash_blocks_study_before_publication(tmp_path):
    _,raw = bundle()
    raw['source_files_sha256'] = {str(tmp_path/'missing'):'0'*64}
    raw['bundle_checksum'] = fingerprint({k:v for k,v in raw.items() if k!='bundle_checksum'})
    path = tmp_path/'inputs.json'
    _write(path,encoded(raw))
    with pytest.raises(FileNotFoundError):
        run_study(path,tmp_path/'results',progress=lambda x:None)
    assert not (tmp_path/'results').exists()
