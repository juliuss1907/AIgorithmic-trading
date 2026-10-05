from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from intraday.replay_v2.donchian_oos_warmup_repair import normalize_rows, ORIGINAL_OPEN, ORIGINAL_CLOSE
from intraday.replay_v2.donchian_oos_config import START


def rows():
    before=ORIGINAL_OPEN-14400000
    return [[before,'100','101','99','100','10',before+14400000-1,'1000',42],
            [ORIGINAL_OPEN,'100','101','99','100','10',ORIGINAL_CLOSE,'1000',42],
            [ORIGINAL_OPEN+14400000,'100','101','99','100','10',ORIGINAL_OPEN+28800000-1,'1000',42]]


@pytest.mark.parametrize('symbol',['BTCUSDT','ETHUSDT','SOLUSDT'])
def test_only_approved_close_time_changes_and_original_is_immutable(symbol):
    raw=rows(); before=deepcopy(raw)
    normalized,log=normalize_rows(symbol,raw,START)
    assert raw==before
    assert normalized[0]==before[0] and normalized[2]==before[2]
    assert normalized[1][:6]==before[1][:6] and normalized[1][7:]==before[1][7:]
    assert normalized[1][6]==ORIGINAL_OPEN+14400000-1
    assert len(log)==1 and log[0]['original_close_time']==ORIGINAL_CLOSE
    assert log[0]['changed_field']=='closeTime' and log[0]['prices_and_volume_unchanged'] is True


@pytest.mark.parametrize('alteration',['symbol','close','open','active','missing','duplicate','other_irregular'])
def test_never_generalize_permission_to_other_data(alteration):
    raw=rows(); symbol='BTCUSDT'; active_start=START
    if alteration=='symbol': symbol='NEARUSDT'
    if alteration=='close': raw[1][6]-=1000
    if alteration=='open': raw[1][0]+=14400000
    if alteration=='active': active_start=datetime.fromtimestamp(ORIGINAL_OPEN/1000,timezone.utc)
    if alteration=='missing': raw.pop(1)
    if alteration=='duplicate': raw.append(deepcopy(raw[1]))
    if alteration=='other_irregular': raw[0][6]-=1000
    with pytest.raises(ValueError):
        normalize_rows(symbol,raw,active_start)


def raw_audit(tmp_path):
    from intraday.replay_v2.artifacts import _write
    from intraday.replay_v2.donchian_filter_data import WARMUP, SPOT_SOURCE
    from intraday.replay_v2.metrics import encoded
    from intraday.replay_v2.portfolio_study import file_hash
    end=START+timedelta(days=1); first=int((START-WARMUP['4h']).timestamp()*1000)
    result=[]
    for symbol in ('BTCUSDT','ETHUSDT','SOLUSDT'):
        values=[[at,'100','101','99','100','10',ORIGINAL_CLOSE if at==ORIGINAL_OPEN else at+14400000-1]
                for at in range(first,int(end.timestamp()*1000),14400000)]
        path=tmp_path/(symbol+'.json')
        _write(path,encoded(dict(symbol=symbol,source=SPOT_SOURCE,window=dict(start_ms=first,end_ms=int(end.timestamp()*1000)),
            accepted_snapshot=False,raw_rows=values,pages=1,fetched_at=(end+timedelta(days=1)).isoformat())))
        result.append(dict(symbol=symbol,raw_path=str(path),raw_sha256=file_hash(path)))
    path=tmp_path/'audit.json'; _write(path,encoded(dict(audits=result)))
    return path,end


def test_full_native_snapshot_and_original_hash_lineage(tmp_path):
    from intraday.replay_v2.donchian_oos_warmup_repair import load_approved
    from intraday.replay_v2.portfolio_study import file_hash
    path,end=raw_audit(tmp_path)
    snapshots,repairs,hashes=load_approved(path,START,end)
    assert len(snapshots)==len(repairs)==3
    assert all(len(snap.raw_rows)==606 for snap in snapshots.values())
    assert all(file_hash(p)==sha for p,sha in hashes.items())
    assert all(r['original_file_sha256']==hashes[r['original_file']] for r in repairs)
    original=tmp_path/'BTCUSDT.json'
    original.write_text(original.read_text()+' ')
    with pytest.raises(ValueError,match='checksum'):
        load_approved(path,START,end)
