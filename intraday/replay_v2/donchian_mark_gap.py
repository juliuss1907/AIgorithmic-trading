"""Approved one-bar native mark gap: stale valuation, never synthetic prices."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from intraday.replay_v2.donchian_filter_data import FilterSnapshot, SOURCES, verify_files
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.intraday_timeframes import IntradayCandle
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.portfolio_study import file_hash


POLICY='binance-mark-m15-audited-20231110-v1'
GAP_OPEN=datetime(2023,11,10,3,45,tzinfo=timezone.utc)
WIDTH=timedelta(minutes=15)
SYMBOLS={'BTCUSDT','ETHUSDT','SOLUSDT'}


class SourceMarkCandle(IntradayCandle):
    """Strict canonical candle type identifies explicitly approved sparse history."""


def mark_candles(rows,symbol,start,end):
    if symbol not in SYMBOLS or not start<end or any(
            at.microsecond or int(at.timestamp())%900 for at in (start,end)):
        raise ValueError('invalid audited mark identity/coverage')
    expected=[at for at in range(int(start.timestamp()*1000),int(end.timestamp()*1000),900000)
              if at!=int(GAP_OPEN.timestamp()*1000)]
    if [r[0] for r in rows]!=expected:
        raise ValueError('mark gap/duplicate outside approved one-bar source gap')
    return tuple(SourceMarkCandle.from_row(row,'15m') for row in rows)


class SparseMarkSnapshot(FilterSnapshot):
    data_policy: Literal['binance-mark-m15-audited-20231110-v1']=POLICY

    def candles(self):
        if self.market!='mark' or self.interval!='15m':
            raise ValueError('approved mark gap applies only to native mark M15')
        return mark_candles(self.raw_rows,self.symbol,self.coverage_start,self.coverage_end)


def stale_symbols(at,observed):
    return [s for s,last in sorted(observed.items()) if GAP_OPEN<=at<GAP_OPEN+WIDTH and last<GAP_OPEN]


def availability(samples):
    return dict(data_policy=POLICY,synthetic_bars=0,missing_opens=[GAP_OPEN.isoformat()],
        stale_valuation_samples=sum(bool(row.get('stale_mark_symbols')) for row in samples),
        valuation='Last actual mark observed; stale only in the audited 15-minute source gap',
        execution='Native trade-price stops/fills and actual funding continue independently',
        drawdown='Known-sample only; unobserved mark moves are not estimated')


def load_approved(audit_path,start,end):
    audit_path=Path(audit_path).expanduser().resolve()
    items=read_inputs(audit_path)['audits']
    if len(items)!=3 or {i['symbol'] for i in items}!=SYMBOLS:
        raise ValueError('requires three immutable audited mark sources')
    hashes={str(audit_path):file_hash(audit_path),**{i['raw_path']:i['raw_sha256'] for i in items}}
    verify_files(hashes)
    snapshots={}
    for item in items:
        raw=read_inputs(item['raw_path'])
        if (raw['symbol']!=item['symbol'] or raw['source']!=SOURCES['mark'] or
                raw['window']!=dict(start_ms=int(start.timestamp()*1000),end_ms=int(end.timestamp()*1000)) or
                raw.get('accepted_snapshot') is not False):
            raise ValueError('audited mark source identity mismatch')
        payload=dict(market='mark',symbol=item['symbol'],interval='15m',source=SOURCES['mark'],
            coverage_start=start.isoformat().replace('+00:00','Z'),coverage_end=end.isoformat().replace('+00:00','Z'),
            fetched_at=datetime.fromisoformat(raw['fetched_at']).isoformat().replace('+00:00','Z'),
            pages=raw['pages'],raw_rows=raw['raw_rows'],data_policy=POLICY)
        snapshots[item['symbol']]=SparseMarkSnapshot(snapshot_id=fingerprint(payload),**payload)
    verify_files(hashes)
    return snapshots,availability([]),hashes
