"""Explicitly approved metadata repair; never manufacture OHLCV or market bars."""

from datetime import datetime, timezone
from pathlib import Path

from intraday.replay_v2.donchian_filter_data import SPOT_SOURCE, WARMUP, snapshot, verify_files
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.portfolio_study import file_hash


POLICY='approved-20210929-spot-h4-warmup-closeTime-v1'
SYMBOLS={'BTCUSDT','ETHUSDT','SOLUSDT'}
ORIGINAL_OPEN=1632888000000
ORIGINAL_CLOSE=1632898799999
WIDTH=14400000


def normalize_rows(symbol,rows,active_start):
    if symbol not in SYMBOLS or ORIGINAL_OPEN>=int(active_start.timestamp()*1000):
        raise ValueError('repair allowed only for the three named Spot H4 warmup buckets')
    normalized=[]; changes=[]
    for row in rows:
        if (not isinstance(row,(list,tuple)) or len(row)<7 or
                type(row[0]) is not int or type(row[6]) is not int or row[0]%WIDTH):
            raise ValueError('invalid raw H4 row; repair permission does not cover it')
        result=list(row)
        if row[0]==ORIGINAL_OPEN:
            if row[6]!=ORIGINAL_CLOSE:
                raise ValueError('approved warmup source closeTime does not match evidence')
            result[6]=ORIGINAL_OPEN+WIDTH-1
            changes.append(dict(policy=POLICY,symbol=symbol,market='spot',interval='4h',
                opened_at=datetime.fromtimestamp(ORIGINAL_OPEN/1000,timezone.utc).isoformat(),
                changed_field='closeTime',original_close_time=ORIGINAL_CLOSE,normalized_close_time=result[6],
                original_row_sha256=fingerprint(row),normalized_row_sha256=fingerprint(result),
                prices_and_volume_unchanged=True,scope='warmup only; conservative availability at bucket end'))
        elif row[6]!=row[0]+WIDTH-1:
            raise ValueError('unapproved H4 timestamp irregularity')
        normalized.append(result)
    if len(changes)!=1:
        raise ValueError('exactly one approved source row per symbol required')
    return normalized,changes


def load_approved(audit_path,start,end):
    """Reuse checksum-bound raw diagnostics, preserving the source file unchanged."""
    audit_path=Path(audit_path).expanduser().resolve()
    audit_sha=file_hash(audit_path); audit=read_inputs(audit_path)
    items=audit['audits']
    if len(items)!=3 or {item['symbol'] for item in items}!=SYMBOLS:
        raise ValueError('requires all three original diagnostic sources')
    hashes={str(audit_path):audit_sha,**{item['raw_path']:item['raw_sha256'] for item in items}}
    verify_files(hashes)
    snapshots={}; repairs=[]
    for item in items:
        raw=read_inputs(item['raw_path']); symbol=item['symbol']
        expected=dict(start_ms=int((start-WARMUP['4h']).timestamp()*1000),end_ms=int(end.timestamp()*1000))
        if (raw['symbol']!=symbol or raw['source']!=SPOT_SOURCE or raw['window']!=expected or
                raw.get('accepted_snapshot') is not False):
            raise ValueError('original diagnostic source identity mismatch')
        rows,log=normalize_rows(symbol,raw['raw_rows'],start)
        snapshots[symbol]=snapshot(market='spot',symbol=symbol,interval='4h',source=SPOT_SOURCE,
            coverage_start=(start-WARMUP['4h']).isoformat().replace('+00:00','Z'),
            coverage_end=end.isoformat().replace('+00:00','Z'),
            fetched_at=datetime.fromisoformat(raw['fetched_at']).isoformat().replace('+00:00','Z'),
            pages=raw['pages'],raw_rows=rows)
        repairs.extend(dict(change,original_file=item['raw_path'],original_file_sha256=item['raw_sha256'],
                            normalized_snapshot_id=snapshots[symbol].snapshot_id) for change in log)
    verify_files(hashes)
    return snapshots,repairs,hashes
