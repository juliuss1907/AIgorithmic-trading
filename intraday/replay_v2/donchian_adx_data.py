"""Immutable joins of approved historical inputs; no strategy or runtime writes."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path

from intraday.replay_v2.donchian_funding_reference import derive
from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_adx_config import ADXStudyConfig
from intraday.replay_v2.donchian_adx_boundaries import JOIN, POLICY
from intraday.replay_v2.donchian_filter_data import (
    FilterSnapshot, WARMUP, decode_bundle, from_futures, verify_files,
)
from intraday.replay_v2.donchian_spot_gap import SparseSpotSnapshot
from intraday.replay_v2.donchian_mark_gap import SparseMarkSnapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


JOIN_MAX_BYTES = 300_000_000  # Four-year native bundle is 229MB; legacy limit stays 200MB.


def read_join_inputs(path):
    fd = os.open(Path(path).expanduser().resolve(), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        body = stream.read(JOIN_MAX_BYTES+1)
    if len(body) > JOIN_MAX_BYTES:
        raise ValueError('joined ADX input bundle exceeds size limit')
    return json.loads(body)


def merge_rows(*segments):
    merged = {}
    for rows in segments:
        seen = set()
        for row in rows:
            at = row[0]
            if at in seen:
                raise ValueError('duplicate native row within a source segment')
            seen.add(at)
            previous = merged.get(at)
            if previous is not None:
                shared = min(len(previous), len(row))
                if list(previous[:shared]) != list(row[:shared]):
                    raise ValueError('conflicting native row in source overlap')
                # Some legacy Spot H4 sources retain only the seven engine columns.
                # Keep the richer original native row, never invent extra fields.
                if len(row) > len(previous):
                    merged[at] = list(row)
            else:
                merged[at] = list(row)
    return [merged[at] for at in sorted(merged)]


def merge_funding(symbol, early, late, start, end, mark):
    merged = {}
    for parent in (early, late):
        if parent['history']['symbol'] != symbol:
            raise ValueError('funding parent identity mismatch')
        for row in parent['raw_rows']:
            at = row['fundingTime']
            if at in merged and merged[at] != row:
                raise ValueError('conflicting funding settlement in source overlap')
            merged[at] = dict(row)
    rows = [r for at,r in sorted(merged.items()) if start.timestamp()*1000 <= at < end.timestamp()*1000]
    observed = max(mark.fetched_at, *(datetime.fromisoformat(p['fetched_at']) for p in (early,late)))
    joined = derive(symbol, rows, start, end, observed, early['pages']+late['pages'], mark)
    if tuple(joined.quote_lineage) != tuple(early.get('quote_lineage',())):
        raise ValueError('joined funding references changed original native row lineage')
    return joined


def collect_join(early_path, late_path, output_root, *, progress=print):
    """Create a new validated bundle; a failed collection never overwrites sources."""
    paths = [Path(p).expanduser().resolve() for p in (early_path,late_path)]
    hashes = {str(p):file_hash(p) for p in paths}
    early, late = (read_inputs(p) for p in paths)
    for parent in (early,late):
        hashes.update(parent['source_files_sha256'])
        cfg = ADXStudyConfig(**{k:datetime.fromisoformat(parent['window'][k]) for k in ('start','end')})
        decoded = decode_bundle(cfg,parent)
        del decoded
    verify_files(hashes)
    if (datetime.fromisoformat(early['window']['end']) != JOIN or
            datetime.fromisoformat(late['window']['start']) != JOIN+timedelta(hours=4)):
        raise ValueError('requires the approved four-hour historical join')
    start,end = datetime.fromisoformat(early['window']['start']),datetime.fromisoformat(late['window']['end'])
    if not start < JOIN < end:
        raise ValueError('invalid joined research window')
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True,mode=0o700,exist_ok=False)
    raw = dict(schema_version='donchian-filters-1',window={'start':start.isoformat(),'end':end.isoformat()},
               candles={},funding={},reuse_lineage={},source_files_sha256=hashes,
               native_boundary_policy=POLICY, data_repairs=early.get('data_repairs',{}),
               join_lineage={'early_bundle_checksum':early['bundle_checksum'],
                             'late_bundle_checksum':late['bundle_checksum'],
                             'native_prices_preserved':True,'synthetic_bars':0})
    for symbol in early['candles']:
        progress(f'{symbol}: fetching only 16 native mark M15 join bars')
        bridge = from_futures(fetch_candle_snapshot(symbol,'15m',JOIN,JOIN+timedelta(hours=4),price_kind='mark'))
        bridge_path = root/(symbol+'-join-mark15m.json')
        _write(bridge_path,encoded(bridge.model_dump(mode='json')))
        hashes[str(bridge_path)] = file_hash(bridge_path)
        raw['candles'][symbol],raw['reuse_lineage'][symbol] = {},{}
        mark = None
        for tag in early['candles'][symbol]:
            parents = [p['candles'][symbol][tag] for p in (early,late)]
            if any((p['symbol'],p['market'],p['interval'],p['source']) !=
                   (parents[0]['symbol'],parents[0]['market'],parents[0]['interval'],parents[0]['source']) for p in parents):
                raise ValueError('native parent snapshot identities differ')
            segments = [p['raw_rows'] for p in parents]
            if tag=='mark15m': segments.append(bridge.raw_rows)
            rows = merge_rows(*segments)
            interval = parents[0]['interval']
            first = start if tag=='mark15m' else start-WARMUP[interval]
            rows = [r for r in rows if first.timestamp()*1000 <= r[0] < end.timestamp()*1000]
            payload = {k:v for k,v in parents[0].items() if k not in ('snapshot_id','raw_rows')}
            payload.update(coverage_start=first.isoformat().replace('+00:00','Z'),
                           coverage_end=end.isoformat().replace('+00:00','Z'),raw_rows=rows,
                           pages=sum(p['pages'] for p in parents)+(bridge.pages if tag=='mark15m' else 0),
                           fetched_at=max(bridge.fetched_at,*(datetime.fromisoformat(p['fetched_at']) for p in parents)).isoformat().replace('+00:00','Z'))
            cls = SparseSpotSnapshot if payload.get('data_policy') and tag=='spot15m' else (
                  SparseMarkSnapshot if payload.get('data_policy') and tag=='mark15m' else FilterSnapshot)
            snap = cls(snapshot_id=fingerprint(payload),**payload)
            raw['candles'][symbol][tag] = snap.model_dump(mode='json')
            raw['reuse_lineage'][symbol][tag] = dict(parent_snapshot_ids=[p['snapshot_id'] for p in parents],
                supplemental_snapshot_id=snap.snapshot_id,active_rows_reused=True,
                join_snapshot_id=bridge.snapshot_id if tag=='mark15m' else None)
            if tag=='mark15m': mark=snap
            progress(f'{symbol} {tag}: {len(rows)} source bars validated')
        funding = merge_funding(symbol,early['funding'][symbol],late['funding'][symbol],start,end,mark)
        raw['funding'][symbol] = funding.model_dump(mode='json')
    raw['bundle_checksum'] = fingerprint(raw)
    cfg = ADXStudyConfig(start=start,end=end)
    decoded = decode_bundle(cfg,raw)
    del decoded
    verify_files(hashes)
    qa = dict(window=raw['window'],bundle_checksum=raw['bundle_checksum'],coverage_validated=True,
              source_unchanged=True,join_lineage=raw['join_lineage'],research_only=True,
              activation_allowed=False,native_boundary_policy=POLICY)
    _write(root/'qa.json',encoded(qa))
    _write(root/'inputs.json',encoded(raw))
    progress('Joined native source coverage, boundaries and funding lineage verified')
    return root/'inputs.json'
