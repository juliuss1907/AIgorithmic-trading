import json
from pathlib import Path

import pytest

from test_donchian_filter_study import bundle
from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.metrics import encoded
from intraday.replay_v2.donchian_oos_pipeline import run, seal, verify_seal
from intraday.replay_v2.donchian_oos_evaluator import verified_receipt


def test_seven_cases_verified_and_no_numeric_progress(tmp_path):
    cfg, raw=bundle()
    inputs=tmp_path/'inputs.json'; _write(inputs,encoded(raw))
    progress=[]
    receipt=run(inputs,tmp_path/'study',progress=progress.append,reference=True)
    assert len(receipt['results'])==7
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert all('net_pnl' not in p and 'final_usdt' not in p for p in progress)
    assert receipt['evaluation']['verdict'] in {'Pass','Fail','Inconclusive'}
    assert verified_receipt(tmp_path/'study'/'comparison.json')['results'][6]['config']['cost_multiplier']==2
    from intraday.replay_v2.donchian_oos_analysis import describe
    analysis=describe(tmp_path/'study'/'comparison.json',tmp_path/'analysis')
    assert analysis['verdict']==receipt['evaluation']
    assert (tmp_path/'analysis'/'report.md').exists()
    with pytest.raises(FileExistsError):
        run(inputs,tmp_path/'study',reference=True)
    # Completed batches are not replayed on resume, even mechanically.
    resumed=run(inputs,tmp_path/'study',reference=True,resume=True,progress=lambda _:None)
    assert resumed==receipt
    # Recover missing verdict artifacts without rerunning any case.
    (tmp_path/'study'/'evaluation.md').unlink()
    run(inputs,tmp_path/'study',reference=True,resume=True,progress=lambda _:None)
    assert (tmp_path/'study'/'evaluation.md').exists()
    altered={**receipt,'evaluation':{**receipt['evaluation'],'verdict':'tampered'}}
    (tmp_path/'study'/'comparison.json').write_text(encoded(altered))
    with pytest.raises(ValueError,match='verdict'):
        run(inputs,tmp_path/'study',reference=True,resume=True,progress=lambda _:None)
    (tmp_path/'study'/'comparison.json').write_text(encoded(receipt))
    saved=Path(receipt['results'][0]['report_directory'])/'trades.jsonl'
    saved.write_text(saved.read_text()+'{}\n')
    with pytest.raises(ValueError):
        verified_receipt(tmp_path/'study'/'comparison.json')


def test_new_window_requires_seal_before_any_output(tmp_path):
    _,raw=bundle(); p=tmp_path/'inputs.json'; _write(p,encoded(raw))
    with pytest.raises(ValueError,match='seal'):
        run(p,tmp_path/'not-created')
    assert not (tmp_path/'not-created').exists()


def test_seal_detects_changed_source(tmp_path, monkeypatch):
    import intraday.replay_v2.donchian_oos_pipeline as module
    repo=tmp_path/'repo'; (repo/'intraday'/'replay_v2').mkdir(parents=True)
    (repo/'docs').mkdir(); (repo/'scripts').mkdir()
    (repo/'intraday'/'replay_v2'/'engine.py').write_text('frozen')
    (repo/'docs'/'donchian-oos-criteria.json').write_text(module.CRITERIA_PATH.read_text())
    (repo/'uv.lock').write_text('locked'); (repo/'pyproject.toml').write_text('project')
    monkeypatch.setattr(module,'git',lambda *args,**kw: 'a'*40 if args[0]=='rev-parse' else '')
    frozen=seal(tmp_path/'seal.json',repo=repo)
    assert verify_seal(tmp_path/'seal.json',repo=repo)==frozen
    (repo/'intraday'/'replay_v2'/'engine.py').write_text('changed')
    with pytest.raises(ValueError,match='changed'):
        verify_seal(tmp_path/'seal.json',repo=repo)
