"""Five-coin manifests and strict public acquisition; never repairs new sources."""

from datetime import datetime, timezone
from pathlib import Path
import time

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_adx_config import START, END, FiveCoinADXConfig
from intraday.replay_v2.donchian_adx_data import read_join_inputs
from intraday.replay_v2.donchian_filter_data import (
    FilterSnapshot, WARMUP, SOURCES, decode_bundle, verify_files,
    fetch_spot_snapshot, public_spot_json, from_futures, decode_snapshot,
)
from intraday.replay_v2.donchian_funding_reference import derive
from intraday.replay_v2.donchian_oos_data import quality
from intraday.replay_v2.donchian_oos_pipeline import publish_once
from intraday.replay_v2.funding import public_funding_json, SOURCE
from intraday.replay_v2.historical_data import fetch_candle_snapshot, public_candle_json, validate_rows
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


SCHEMA = 'donchian-five-manifest-1'
NEW_SYMBOLS = ('NEARUSDT', 'ZECUSDT')
TAGS = ('spot4h', 'spot15m', 'perp4h', 'perp15m', 'mark15m')


def reference(path, snapshot_id=None):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=file_hash(path), snapshot_id=snapshot_id)


def load_reference(ref, *, joined=False):
    verify_files({ref['path']: ref['sha256']})
    raw = (read_join_inputs if joined else read_inputs)(ref['path'])
    if ref['snapshot_id'] is not None and ref['snapshot_id'] != raw.get('snapshot_id', raw.get('funding_id')):
        raise ValueError('manifest snapshot identity mismatch')
    return raw


def read_manifest(path):
    manifest = read_inputs(path)
    if (manifest.get('schema_version') != SCHEMA or manifest.get('manifest_checksum') !=
            fingerprint({k:v for k,v in manifest.items() if k != 'manifest_checksum'})):
        raise ValueError('five-coin manifest checksum/schema mismatch')
    if set(manifest['additions']) != set(NEW_SYMBOLS):
        raise ValueError('five-coin manifest requires NEAR and ZEC')
    approval=load_reference(manifest['approved_sources']) if 'approved_sources' in manifest else None
    audit=None
    if approval is not None:
        from intraday.replay_v2.donchian_five_policy import POLICY,disclosure
        if approval.get('approved') is not True or approval.get('policy')!=POLICY or approval['policy_checksum']!=fingerprint(disclosure()):
            raise ValueError('new coin source approval policy changed')
        audit=load_reference(approval['audit'])
        verify_files(audit['source_files_sha256'])
    raw = load_reference(manifest['base'], joined=True)
    if raw['bundle_checksum'] != fingerprint({k:v for k,v in raw.items() if k != 'bundle_checksum'}):
        raise ValueError('base bundle checksum mismatch')
    if set(raw['candles']) != {'BTCUSDT','ETHUSDT','SOLUSDT'} or raw['window'] != manifest['window']:
        raise ValueError('base universe/window mismatch')
    raw['source_files_sha256'][manifest['base']['path']] = manifest['base']['sha256']
    if approval is not None:
        raw['native_boundary_policy']=approval['policy']
        raw['source_files_sha256'].update(audit['source_files_sha256'])
        for ref in (manifest['approved_sources'],approval['audit'],approval['proposal']):
            verify_files({ref['path']:ref['sha256']})
            raw['source_files_sha256'][ref['path']]=ref['sha256']
    for symbol, refs in manifest['additions'].items():
        if set(refs) != {*TAGS, 'funding'}:
            raise ValueError('manifest missing native series or funding')
        raw['candles'][symbol] = {}; raw['reuse_lineage'][symbol] = {}
        for tag, ref in refs.items():
            payload = load_reference(ref)
            raw['source_files_sha256'][ref['path']] = ref['sha256']
            if tag == 'funding':
                raw['funding'][symbol] = payload
            else:
                # New coins must remain dense even when old coins carry policies.
                if 'data_policy' in payload and (approval is None or payload['data_policy']!=approval['policy']):
                    raise ValueError('new coin source exception requires separate approval')
                raw['candles'][symbol][tag] = payload
                raw['reuse_lineage'][symbol][tag] = dict(snapshot_id=ref['snapshot_id'], source=payload['source'])
            if approval is not None:
                original=(audit['coins'][symbol]['series'][tag]['path'] if tag!='funding' else
                          str(Path(audit['coins'][symbol]['series']['mark15m']['path']).parent/f'{symbol}-funding-raw.json'))
                original_digest=audit['source_files_sha256'][original]
                source_raw=load_reference(dict(path=original,sha256=original_digest,snapshot_id=None))
                if payload['raw_rows']!=source_raw['raw_rows']:
                    raise ValueError('approved snapshot changed original source rows')
                if 'original_source_sha256' in payload and payload['original_source_sha256']!=original_digest:
                    raise ValueError('approved snapshot original-source binding mismatch')
                if tag!='funding':
                    raw['reuse_lineage'][symbol][tag].update(original_raw_path=original,original_raw_sha256=original_digest)
    verify_files(manifest['source_files_sha256'])
    raw['source_files_sha256'].update(manifest['source_files_sha256'])
    raw['source_files_sha256'][str(Path(path).resolve())] = file_hash(path)
    raw['bundle_checksum'] = fingerprint({k:v for k,v in raw.items() if k != 'bundle_checksum'})
    return raw


def collect_approved(approval_path, base_path, output_root, *, resume=False, progress=print):
    """Freeze the separately approved complete raw sources; no network or strategy."""
    from intraday.replay_v2.donchian_five_policy import POLICY,ApprovedFiveSnapshot,disclosure
    approval=read_inputs(approval_path)
    if approval.get('approved') is not True or approval['policy']!=POLICY or approval['policy_checksum']!=fingerprint(disclosure()):
        raise ValueError('explicit exact added-coin source approval required')
    audit=load_reference(approval['audit']);verify_files(audit['source_files_sha256'])
    root=Path(output_root).expanduser().resolve();root.mkdir(parents=True,mode=0o700,exist_ok=resume)
    binding=dict(approved_sources=reference(approval_path),base=reference(base_path))
    publish_once(root/'binding.json',encoded(binding))
    additions={}
    for symbol in NEW_SYMBOLS:
        additions[symbol]={};mark=None
        for tag in TAGS:
            original=audit['coins'][symbol]['series'][tag]
            source=load_reference(dict(path=original['path'],sha256=original['sha256'],snapshot_id=None))
            if tag.startswith('perp'):
                snap=FilterSnapshot.model_validate(source)
            else:
                interval='4h' if tag.endswith('4h') else '15m'
                market='spot' if tag.startswith('spot') else 'mark'
                payload=dict(market=market,symbol=symbol,interval=interval,source=source['source'],
                    coverage_start=source['start'].replace('+00:00','Z'),coverage_end=source['end'].replace('+00:00','Z'),
                    fetched_at=source['fetched_at'].replace('+00:00','Z'),pages=source['pages'],raw_rows=source['raw_rows'],
                    data_policy=POLICY,original_source_sha256=original['sha256'])
                snap=ApprovedFiveSnapshot(snapshot_id=fingerprint(payload),**payload)
            path=root/f'{symbol}-{tag}.json';publish_once(path,encoded(snap.model_dump(mode='json')))
            additions[symbol][tag]=reference(path,snap.snapshot_id)
            if tag=='mark15m':mark=snap
            progress(f'{symbol} {tag}: {len(snap.raw_rows)} original rows validated under approved policy')
        funding_path=Path(audit['coins'][symbol]['series']['mark15m']['path']).parent/f'{symbol}-funding-raw.json'
        source=load_reference(dict(path=str(funding_path),sha256=audit['source_files_sha256'][str(funding_path)],snapshot_id=None))
        funding=derive(symbol,source['raw_rows'],START,END,datetime.fromisoformat(source['fetched_at']),source['pages'],mark)
        path=root/f'{symbol}-funding.json';publish_once(path,encoded(funding.model_dump(mode='json')))
        additions[symbol]['funding']=reference(path,funding.funding_id)
    manifest=dict(schema_version=SCHEMA,window=dict(start=START.isoformat(),end=END.isoformat()),
                  **binding,additions=additions,source_files_sha256={str(root/'binding.json'):file_hash(root/'binding.json')})
    manifest['manifest_checksum']=fingerprint(manifest)
    candidate=root/'candidate-manifest.json';publish_once(candidate,encoded(manifest))
    config=FiveCoinADXConfig(start=START,end=END)
    data,funding=decode_bundle(config,read_manifest(candidate))
    qa=quality(config,data,funding);qa['new_coin_data_policy']=disclosure()
    qa['strict_checks']='passed: only individually approved native exceptions; all source rows retained; derived H4 interval metadata disclosed'
    publish_once(root/'qa.json',encoded(qa))
    publish_once(root/'inputs.json',encoded(manifest))
    progress('Approved source coverage, OHLCV, exact boundaries and funding verified; no strategy run')
    return root/'inputs.json'


def page_issues(rows, interval):
    width = 14400000 if interval == '4h' else 900000
    timestamps = [r[0] for r in rows]
    gaps = [dict(after=a, before=b, missing=(b-a)//width-1)
            for a,b in zip(timestamps,timestamps[1:]) if b-a != width]
    metadata = [dict(open_ms=r[0], close_ms=r[6], expected_close_ms=r[0]+width-1)
                for r in rows if r[6] != r[0]+width-1]
    return dict(gaps_or_duplicates=gaps, noncanonical_close_times=metadata)


def audit_raw_series(symbol, tag, root):
    """Acquire ALL original rows for review only; gaps never become accepted inputs."""
    interval='4h' if tag.endswith('4h') else '15m'
    market='spot' if tag.startswith('spot') else 'mark' if tag=='mark15m' else 'perp'
    start=START if tag=='mark15m' else START-WARMUP[interval]
    width=14400000 if interval=='4h' else 900000
    path=Path(root)/f'{symbol}-{tag}-raw-audit.json'
    if path.exists():
        evidence=read_inputs(path)
        if (evidence['symbol'],evidence['tag'],evidence['source'],evidence['start'],evidence['end']) != (
                symbol,tag,SOURCES[market],start.isoformat(),END.isoformat()):
            raise ValueError('raw audit checkpoint identity mismatch')
        return evidence
    rows=[]; pages=0; cursor=int(start.timestamp()*1000); finish=int(END.timestamp()*1000)
    while cursor<finish:
        query=dict(symbol=symbol,interval=interval,startTime=cursor,endTime=finish-1,limit=1000)
        page=(public_spot_json(query) if market=='spot' else
              public_candle_json('/fapi/v1/markPriceKlines' if market=='mark' else '/fapi/v1/klines',query))
        pages+=1
        if not isinstance(page,list) or len(page)>1000:
            raise ValueError('invalid raw audit response')
        if not page: break
        if any(len(row)<7 or isinstance(row[0],bool) or not isinstance(row[0],int) or
               not cursor<=row[0]<finish for row in page) or any(a[0]>=b[0] for a,b in zip(page,page[1:])):
            raise ValueError('raw audit response has unordered, duplicate or out-of-window timestamps')
        rows.extend(page); cursor=page[-1][0]+width
        time.sleep(.1)
    evidence=dict(symbol=symbol,tag=tag,source=SOURCES[market],start=start.isoformat(),end=END.isoformat(),
                  fetched_at=datetime.now(timezone.utc).isoformat(),pages=pages,raw_rows=rows,
                  accepted_snapshot=False,research_strategy_runs=0,
                  issues=page_issues(rows,interval),
                  missing_opens_ms=sorted(set(range(int(start.timestamp()*1000),finish,width))-{r[0] for r in rows}))
    _write(path,encoded(evidence))
    return evidence


def collect_series(symbol, tag, root):
    interval = '4h' if tag.endswith('4h') else '15m'
    first = START if tag == 'mark15m' else START-WARMUP[interval]
    path = root/f'{symbol}-{tag}.json'
    if path.exists():
        snap = FilterSnapshot.model_validate(read_inputs(path))
    else:
        def observed_page(query, futures_path=None):
            rows = public_spot_json(query) if futures_path is None else public_candle_json(futures_path, query)
            width = 14400000 if interval == '4h' else 900000
            if isinstance(rows, list) and rows:
                try:
                    validate_rows(rows, interval, datetime.fromtimestamp(query['startTime']/1000, timezone.utc),
                                  datetime.fromtimestamp((query['startTime']+len(rows)*width)/1000, timezone.utc))
                except ValueError as error:
                    rejected = root/f'{symbol}-{tag}-rejected-{query["startTime"]}.json'
                    evidence = dict(symbol=symbol, tag=tag, source=SOURCES['spot' if tag.startswith('spot') else 'mark' if tag=='mark15m' else 'perp'],
                                    fetched_at=datetime.now(timezone.utc).isoformat(),
                                    query=query, raw_rows=rows, accepted_snapshot=False, error=str(error),
                                    issues=page_issues(rows, interval))
                    if not rejected.exists(): _write(rejected, encoded(evidence))
                    raise ValueError(f'{symbol} {tag}: {error}; evidence {rejected}') from error
            time.sleep(.1)
            return rows
        if tag.startswith('spot'):
            snap = fetch_spot_snapshot(symbol, interval, first, END, fetch_json=observed_page)
        else:
            snap = from_futures(fetch_candle_snapshot(symbol, interval, first, END,
                price_kind='mark' if tag=='mark15m' else 'trade',
                fetch_json=lambda endpoint, query: observed_page(query, endpoint)))
        _write(path, encoded(snap.model_dump(mode='json')))
    market = 'spot' if tag.startswith('spot') else 'mark' if tag=='mark15m' else 'perp'
    if (snap.symbol,snap.market,snap.interval,snap.coverage_start,snap.coverage_end) != (symbol,market,interval,first,END):
        raise ValueError('five-coin checkpoint identity mismatch')
    return reference(path, snap.snapshot_id)


def collect_funding(symbol, root, mark):
    raw_path = root/f'{symbol}-funding-raw.json'
    if raw_path.exists():
        evidence = read_inputs(raw_path)
    else:
        rows=[]; cursor=int(START.timestamp()*1000); finish=int(END.timestamp()*1000)
        pages=0
        while cursor < finish:
            page=public_funding_json(dict(symbol=symbol,startTime=cursor,endTime=finish-1,limit=1000))
            if not isinstance(page,list) or len(page)>1000:
                raise ValueError('invalid funding response')
            pages+=1
            if not page: break
            for row in page:
                at=row.get('fundingTime')
                if row.get('symbol')!=symbol or isinstance(at,bool) or not isinstance(at,int) or not cursor<=at<finish:
                    raise ValueError('funding identity/order/window mismatch')
                cursor=at+1
            rows.extend(page)
            if len(page)<1000: break
            time.sleep(.1)
        evidence=dict(symbol=symbol,source=SOURCE,start=START.isoformat(),end=END.isoformat(),
                      fetched_at=datetime.now(timezone.utc).isoformat(),pages=pages,raw_rows=rows)
        _write(raw_path,encoded(evidence))
    if (evidence['symbol'],evidence['source'],evidence['start'],evidence['end']) != (symbol,SOURCE,START.isoformat(),END.isoformat()):
        raise ValueError('funding checkpoint identity mismatch')
    if mark is None:
        raise ValueError('funding raw retained; native mark failed strict QA; no price substitution permitted')
    snap=derive(symbol,evidence['raw_rows'],START,END,datetime.fromisoformat(evidence['fetched_at']),evidence['pages'],mark)
    path=root/f'{symbol}-funding.json'
    if path.exists():
        if read_inputs(path)!=snap.model_dump(mode='json'): raise ValueError('funding checkpoint changed')
    else: _write(path,encoded(snap.model_dump(mode='json')))
    return reference(path,snap.funding_id)


def collect(base_path, output_root, *, resume=False, progress=print):
    root=Path(output_root).expanduser().resolve()
    root.mkdir(parents=True,mode=0o700,exist_ok=resume)
    binding=dict(base=reference(base_path),window=dict(start=START.isoformat(),end=END.isoformat()),
                 configs=FiveCoinADXConfig(start=START,end=END).model_dump(mode='json'))
    if (root/'binding.json').exists():
        if read_inputs(root/'binding.json')!=binding: raise ValueError('collection binding changed')
    else: _write(root/'binding.json',encoded(binding))
    if (root/'inputs.json').exists():
        raw=read_manifest(root/'inputs.json')
        decode_bundle(FiveCoinADXConfig(start=START,end=END),raw)
        return root/'inputs.json'
    additions={}; errors=[]
    for symbol in NEW_SYMBOLS:
        additions[symbol]={}
        for tag in TAGS:
            progress(f'Collect/verify {symbol} {tag}')
            try: additions[symbol][tag]=collect_series(symbol,tag,root)
            except (ValueError, OSError) as error:
                errors.append(dict(symbol=symbol,series=tag,error=str(error)))
                progress(str(error))
        mark_ref=additions[symbol].get('mark15m')
        mark=FilterSnapshot.model_validate(load_reference(mark_ref)) if mark_ref else None
        try: additions[symbol]['funding']=collect_funding(symbol,root,mark)
        except (ValueError, OSError) as error:
            errors.append(dict(symbol=symbol,series='funding',error=str(error)))
            progress(str(error))
    if errors:
        evidence=dict(status='blocked',strategy_runs=0,exceptions_approved=False,errors=errors,
                      files={str(p):file_hash(p) for p in root.glob('*.json')})
        path=root/f'blocked-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}.json'
        _write(path,encoded(evidence))
        raise ValueError(f'Unapproved source data errors; no manifest or strategy results. See {path}')
    manifest=dict(schema_version=SCHEMA,base=binding['base'],window=binding['window'],additions=additions,
                  source_files_sha256={str(p):file_hash(p) for p in root.glob('*-raw.json')})
    manifest['manifest_checksum']=fingerprint(manifest)
    # Candidate is retained if cross-series QA fails; only inputs.json authorizes replay.
    candidate=root/'candidate-manifest.json'; publish_once(candidate,encoded(manifest))
    config=FiveCoinADXConfig(start=START,end=END)
    data,funding=decode_bundle(config,read_manifest(candidate))
    qa=quality(config,data,funding)
    publish_once(root/'qa.json',encoded(qa))
    publish_once(root/'inputs.json',encoded(manifest))
    return root/'inputs.json'


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-inputs',required=True)
    parser.add_argument('--output-root',required=True)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--approved-sources',help='Explicit checksum-bound source approval receipt; offline acquisition from full audited rows')
    args=parser.parse_args()
    if args.approved_sources:
        collect_approved(args.approved_sources,args.base_inputs,args.output_root,resume=args.resume)
    else:
        collect(args.base_inputs,args.output_root,resume=args.resume)


if __name__=='__main__': main()
