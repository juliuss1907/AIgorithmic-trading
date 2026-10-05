"""Approved funding-rate history with explicitly derived native-open price references."""

from datetime import datetime,timezone
from pathlib import Path
from typing import Literal

from pydantic import Field,model_validator

from intraday.replay_v2.funding import FundingSnapshot,SOURCE,_parse_rows
from intraday.replay_v2.contracts import FundingHistory,utc
from intraday.replay_v2.intraday_timeframes import IntradayCandle
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.portfolio_study import file_hash
from intraday.replay_v2.donchian_filter_data import verify_files


POLICY='approved-funding-native-mark-open-31ms-v1'


def priced_rows(rows,quotes, *, symbol=None):
    derived=[]; used=0
    for raw in rows:
        row=dict(raw)
        if row.get('markPrice') in (None,''):
            at=row.get('fundingTime')
            last = 1790899199999 if symbol in ('NEARUSDT','ZECUSDT') else 1698710400031
            if isinstance(at,bool) or not isinstance(at,int) or not 1640995200000<=at<=last:
                raise ValueError('unapproved missing funding quote period')
            if used>=len(quotes): raise ValueError('missing funding quote lineage')
            q=quotes[used]; used+=1
            source=q['mark_row']; IntradayCandle.from_row(source,'15m')
            age=at-source[0]
            if not 0<=age<=31 or q['age_ms']!=age or q['funding_time_ms']!=at:
                raise ValueError('native funding reference must be causal and at most 31ms old')
            if q['mark_row_sha256']!=fingerprint(source):
                raise ValueError('funding mark-row checksum mismatch')
            row['markPrice']=source[1]
        derived.append(row)
    if used!=len(quotes): raise ValueError('unused funding quote lineage')
    return derived


class ReferenceFundingHistory(FundingHistory):
    data_policy: Literal['approved-funding-native-mark-open-31ms-v1']=POLICY
    mark_snapshot_id: str=Field(pattern=r'^[a-f0-9]{64}$')
    reference_times_ms: tuple[int,...]


class ReferenceFundingSnapshot(FundingSnapshot):
    history: ReferenceFundingHistory
    data_policy: Literal['approved-funding-native-mark-open-31ms-v1']=POLICY
    mark_snapshot_id: str=Field(pattern=r'^[a-f0-9]{64}$')
    quote_lineage: tuple[dict,...]

    @model_validator(mode='after')
    def verify(self):
        if self.schema_version!='1' or self.history.source!=SOURCE or utc(self.fetched_at)<self.history.coverage_end:
            raise ValueError('invalid derived funding source/coverage')
        if self.funding_id!=fingerprint(self.model_dump(mode='json',exclude={'funding_id'})):
            raise ValueError('derived funding checksum mismatch')
        rows=priced_rows(self.raw_rows,self.quote_lineage,symbol=self.history.symbol)
        settlements=_parse_rows(rows,self.history.symbol,self.history.coverage_start,self.history.coverage_end)
        if not settlements or settlements!=self.history.settlements:
            raise ValueError('derived funding evidence mismatch')
        if (self.history.mark_snapshot_id!=self.mark_snapshot_id or self.history.reference_times_ms!=tuple(
                q['funding_time_ms'] for q in self.quote_lineage)):
            raise ValueError('funding history price-reference provenance mismatch')
        return self


def derive(symbol,rows,start,end,fetched_at,pages,mark):
    if mark.market!='mark' or mark.symbol!=symbol or mark.coverage_start>start or mark.coverage_end<end:
        raise ValueError('wrong native mark reference coverage/identity')
    opens={r[0]:r for r in mark.raw_rows}; quotes=[]
    for row in rows:
        if row.get('markPrice') in (None,''):
            at=row['fundingTime']; source=opens.get(at-at%900000)
            if source is None: raise ValueError('missing actual native mark open for funding')
            quotes.append(dict(funding_time_ms=at,age_ms=at-source[0],mark_row=list(source),
                               mark_row_sha256=fingerprint(source)))
    settlements=_parse_rows(priced_rows(rows,quotes,symbol=symbol),symbol,start,end)
    history=ReferenceFundingHistory(symbol=symbol,source=SOURCE,coverage_start=start,coverage_end=end,
        settlements=settlements,mark_snapshot_id=mark.snapshot_id,reference_times_ms=tuple(q['funding_time_ms'] for q in quotes))
    payload=dict(schema_version='1',history=history.model_dump(mode='json'),
        fetched_at=utc(fetched_at).isoformat().replace('+00:00','Z'),pages=pages,raw_rows=rows,
        data_policy=POLICY,mark_snapshot_id=mark.snapshot_id,quote_lineage=quotes)
    return ReferenceFundingSnapshot(funding_id=fingerprint(payload),**payload)


def decode(payload,mark):
    snap=ReferenceFundingSnapshot.model_validate(payload)
    actual={r[0]:r for r in mark.raw_rows}
    if (snap.mark_snapshot_id!=mark.snapshot_id or snap.history.symbol!=mark.symbol or
            mark.market!='mark' or mark.coverage_start>snap.history.coverage_start or
            mark.coverage_end<snap.history.coverage_end or any(
                tuple(q['mark_row'])!=actual.get(q['mark_row'][0]) for q in snap.quote_lineage)):
        raise ValueError('funding quotes do not bind actual native mark source')
    return snap


def load_approved(audit_path,start,end,marks):
    audit_path=Path(audit_path).expanduser().resolve(); items=read_inputs(audit_path)['audits']
    if len(items)!=3 or {i['symbol'] for i in items}!=set(marks):
        raise ValueError('requires all three audited funding sources')
    hashes={str(audit_path):file_hash(audit_path),**{i['raw_path']:i['raw_sha256'] for i in items}}
    verify_files(hashes); snapshots={}
    for item in items:
        raw=read_inputs(item['raw_path']); symbol=item['symbol']
        if (raw['source']!=SOURCE or raw['symbol']!=symbol or raw.get('accepted_snapshot') is not False or
                raw['window']!=dict(start_ms=int(start.timestamp()*1000),end_ms=int(end.timestamp()*1000))):
            raise ValueError('audited funding source identity mismatch')
        snapshots[symbol]=derive(symbol,raw['raw_rows'],start,end,
            datetime.fromisoformat(raw['fetched_at']),raw['pages'],marks[symbol])
    verify_files(hashes)
    return snapshots,hashes
