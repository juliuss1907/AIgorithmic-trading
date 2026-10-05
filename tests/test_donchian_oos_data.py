from pathlib import Path
from datetime import timedelta

import pytest

from test_donchian_filter_study import bundle
from intraday.replay_v2.donchian_oos_data import collect
from intraday.replay_v2.donchian_filter_data import FilterSnapshot, decode_bundle
from intraday.replay_v2.funding import FundingSnapshot


def test_collector_freezes_raw_checkpoints_and_resume(monkeypatch,tmp_path):
    import intraday.replay_v2.donchian_oos_data as module
    cfg,raw=bundle()
    monkeypatch.setattr(module,'START',cfg.start); monkeypatch.setattr(module,'END',cfg.end)
    monkeypatch.setattr(module,'verify_seal',lambda _: {'seal_checksum':'a'*64,'reference_path':'old'})
    def candles(symbol,interval,start,end,*,price_kind=None):
        market=price_kind or 'spot'
        tag=('perp' if market=='trade' else 'mark' if market=='mark' else 'spot')+interval
        return FilterSnapshot.model_validate(raw['candles'][symbol][tag])
    def funding(symbol,start,end):
        return FundingSnapshot.model_validate(raw['funding'][symbol])
    monkeypatch.setattr(module,'from_futures',lambda snap:snap)
    p=collect('seal',tmp_path/'data',spot_fetcher=candles,perp_fetcher=candles,
              funding_fetcher=funding,progress=lambda _:None)
    assert p.exists()
    import json
    assert len(decode_bundle(cfg,json.loads(p.read_text()))[0])==3
    assert (tmp_path/'data'/'qa.json').exists()
    # Completed collection revalidates and returns without external calls.
    def no_call(*args,**kwargs):
        raise AssertionError('unexpected refetch')
    assert collect('seal',tmp_path/'data',resume=True,spot_fetcher=no_call,
                   perp_fetcher=no_call,funding_fetcher=no_call,progress=lambda _:None)==p
    with pytest.raises(FileExistsError):
        collect('seal',tmp_path/'data',progress=lambda _:None)


def test_collection_requires_golden_reference(monkeypatch,tmp_path):
    import intraday.replay_v2.donchian_oos_data as module
    monkeypatch.setattr(module,'verify_seal',lambda _: {'seal_checksum':'a'*64})
    with pytest.raises(ValueError,match='reference'):
        collect('seal',tmp_path/'not-created')
    assert not (tmp_path/'not-created').exists()


def test_resume_after_qa_publication_interruption(monkeypatch,tmp_path):
    import intraday.replay_v2.donchian_oos_data as module
    cfg,raw=bundle()
    monkeypatch.setattr(module,'START',cfg.start); monkeypatch.setattr(module,'END',cfg.end)
    monkeypatch.setattr(module,'verify_seal',lambda _: {'seal_checksum':'a'*64,'reference_path':'old'})
    def candles(symbol,interval,start,end,*,price_kind=None):
        prefix='spot' if price_kind is None else 'perp' if price_kind=='trade' else 'mark'
        return FilterSnapshot.model_validate(raw['candles'][symbol][prefix+interval])
    def funding(symbol,start,end):
        return FundingSnapshot.model_validate(raw['funding'][symbol])
    monkeypatch.setattr(module,'from_futures',lambda snap:snap)
    write=module._write
    def interrupt(path,text):
        if path.name=='inputs.json':
            raise OSError('simulated interruption')
        return write(path,text)
    monkeypatch.setattr(module,'_write',interrupt)
    with pytest.raises(OSError,match='interruption'):
        collect('seal',tmp_path/'data',spot_fetcher=candles,perp_fetcher=candles,
                funding_fetcher=funding,progress=lambda _:None)
    monkeypatch.setattr(module,'_write',write)
    assert collect('seal',tmp_path/'data',resume=True,spot_fetcher=candles,perp_fetcher=candles,
                   funding_fetcher=funding,progress=lambda _:None).exists()
