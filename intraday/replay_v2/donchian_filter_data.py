"""Explicit public acquisition; immutable native Spot/Perp inputs for offline study."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from typing import Literal

from pydantic import Field, field_validator, model_validator

from intraday.assets import ticker_symbol
from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.contracts import FrozenModel, utc
from intraday.replay_v2.funding import NoRedirect, FundingSnapshot
from intraday.replay_v2.historical_data import CandleSnapshot, fetch_candle_snapshot, validate_rows
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.intraday_data import verify_boundaries
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


SPOT_SOURCE = 'https://api.binance.com/api/v3/klines'
SOURCES = {'spot':SPOT_SOURCE, 'perp':'https://fapi.binance.com/fapi/v1/klines',
           'mark':'https://fapi.binance.com/fapi/v1/markPriceKlines'}
WARMUP = {'4h':timedelta(hours=2400), '15m':timedelta(hours=484)}


class FilterSnapshot(FrozenModel):
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    market: Literal['spot', 'perp', 'mark']
    symbol: str
    interval: Literal['4h', '15m']
    source: str
    coverage_start: datetime
    coverage_end: datetime
    fetched_at: datetime
    pages: int = Field(ge=0)
    raw_rows: tuple[tuple, ...]

    _symbol = field_validator('symbol')(ticker_symbol)
    _times = field_validator('coverage_start', 'coverage_end', 'fetched_at')(utc)

    @model_validator(mode='after')
    def verify(self):
        if self.source != SOURCES[self.market] or not self.coverage_start < self.coverage_end <= self.fetched_at:
            raise ValueError('invalid native filter snapshot source or coverage')
        if self.snapshot_id != fingerprint(self.model_dump(mode='json', exclude={'snapshot_id'})):
            raise ValueError('filter snapshot checksum mismatch')
        width = timedelta(hours=4) if self.interval == '4h' else timedelta(minutes=15)
        if any(at.microsecond or int(at.timestamp()) % int(width.total_seconds()) for at in (
                self.coverage_start, self.coverage_end)):
            raise ValueError('unaligned filter snapshot coverage')
        self.candles()
        return self

    def candles(self):
        return validate_rows(self.raw_rows, self.interval, self.coverage_start, self.coverage_end)


def snapshot(**payload):
    return FilterSnapshot(snapshot_id=fingerprint(payload), **payload)


def public_spot_json(query):
    request = Request(SPOT_SOURCE+'?'+urlencode(query), headers={'User-Agent':'aigt-filter-research/1'})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        body = response.read(20_000_001)
    if len(body) > 20_000_000:
        raise ValueError('Spot history response exceeds size limit')
    return json.loads(body)


def fetch_spot_snapshot(symbol, interval, start, end, *, fetch_json=public_spot_json, page_size=1000, now=None):
    symbol, start, end = ticker_symbol(symbol), utc(start), utc(end)
    observed = utc(now or datetime.now(timezone.utc))
    if interval not in WARMUP or not 1 <= page_size <= 1000 or not start < end <= observed:
        raise ValueError('invalid Spot history collection parameters')
    width = 14_400_000 if interval == '4h' else 900_000
    cursor, finish = int(start.timestamp()*1000), int(end.timestamp()*1000)
    if any(at.microsecond or int(at.timestamp()*1000) % width for at in (start, end)):
        raise ValueError('unaligned Spot collection window')
    rows, pages = [], 0
    while cursor < finish:
        limit = min(page_size, (finish-cursor)//width)
        page = fetch_json(dict(symbol=symbol, interval=interval, startTime=cursor, endTime=finish-1, limit=limit))
        if not isinstance(page, list) or not page or len(page) > limit:
            raise ValueError('incomplete Spot history response')
        last = cursor+len(page)*width
        validate_rows(page, interval, datetime.fromtimestamp(cursor/1000, timezone.utc),
                      datetime.fromtimestamp(last/1000, timezone.utc))
        rows.extend(page)
        pages += 1
        cursor = last
        if fetch_json is public_spot_json and cursor < finish:
            time.sleep(.1)
    return snapshot(market='spot', symbol=symbol, interval=interval, source=SPOT_SOURCE,
        coverage_start=start.isoformat().replace('+00:00','Z'), coverage_end=end.isoformat().replace('+00:00','Z'),
        fetched_at=observed.isoformat().replace('+00:00','Z'), pages=pages, raw_rows=rows)


def from_futures(parent):
    payload = parent.model_dump(mode='json', exclude={'snapshot_id', 'price_kind'})
    return snapshot(market='perp' if parent.price_kind == 'trade' else 'mark', **payload)


def extend(parent, start, end, fetcher):
    if not parent.coverage_start < end <= parent.coverage_end:
        raise ValueError('frozen parent does not cover active research window')
    prefix = fetcher(parent.symbol, parent.interval, start, parent.coverage_start) if start < parent.coverage_start else None
    first, last = int(start.timestamp()*1000), int(end.timestamp()*1000)
    suffix = tuple(row for row in parent.raw_rows if first <= row[0] < last)
    payload = parent.model_dump(mode='json', exclude={'snapshot_id'})
    payload.update(coverage_start=start.isoformat().replace('+00:00','Z'),
        coverage_end=end.isoformat().replace('+00:00','Z'), raw_rows=(prefix.raw_rows if prefix else ())+suffix,
        fetched_at=max(parent.fetched_at, prefix.fetched_at if prefix else parent.fetched_at).isoformat().replace('+00:00','Z'),
        pages=parent.pages+(prefix.pages if prefix else 0))
    return snapshot(**payload)


def verify_retained(snap, parent, start, end):
    retained = {r[0]:r for r in snap.raw_rows}
    first, last = int(start.timestamp()*1000), int(end.timestamp()*1000)
    if any(retained.get(r[0]) != r for r in parent.raw_rows if first <= r[0] < last):
        raise ValueError('supplemental input changed frozen parent rows')


def collect(frozen_root, sol_inputs, output_root, *, resume=False, reuse_root=None, progress=print,
            spot_fetcher=fetch_spot_snapshot, perp_fetcher=fetch_candle_snapshot):
    old, sol, root = (Path(p).expanduser().resolve() for p in (frozen_root, sol_inputs, output_root))
    receipt = read_inputs(old/'comparison.json')
    if receipt.get('preset') != 'perp-short-reserve':
        raise ValueError('requires canonical frozen short-reserve input study')
    start, end = (datetime.fromisoformat(receipt['window'][key]) for key in ('start','end'))
    source_hashes = {str(old/name):digest for name,digest in receipt['snapshot_files_sha256'].items()}
    source_hashes.update({str(old/'comparison.json'):file_hash(old/'comparison.json'),
        receipt['original_source']:receipt['source_sha256'],
        **{str(sol/name):file_hash(sol/name) for name in ('perp-inputs.json','intraday-inputs.json')}})
    verify_files(source_hashes)
    if root.exists() and (not resume or (root/'inputs.json').exists()):
        raise FileExistsError(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=resume)
    raw_spot = read_inputs(old/'spot-inputs.json')
    original_time = datetime.fromtimestamp((old/'spot-inputs.json').stat().st_mtime, timezone.utc)
    raw_perp = [read_inputs(p/'perp-inputs.json') for p in (old, sol)]
    raw_native = [read_inputs(p/'intraday-inputs.json') for p in (old, sol)]
    for raw in (*raw_perp, *raw_native):
        if raw['bundle_checksum'] != fingerprint({k:v for k,v in raw.items() if k != 'bundle_checksum'}) or raw['window'] != receipt['window']:
            raise ValueError('frozen parent bundle checksum/window mismatch')
    raw = dict(schema_version='donchian-filters-1', window=receipt['window'], source_files_sha256=source_hashes,
               candles={}, funding={}, reuse_lineage={})
    for symbol in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT'):
        base = raw_perp[1 if symbol == 'SOLUSDT' else 0]
        native = raw_native[1 if symbol == 'SOLUSDT' else 0]
        spot_rows = raw_spot['candles'][symbol]
        spot_parent = snapshot(market='spot', symbol=symbol, interval='4h', source=SPOT_SOURCE,
            coverage_start=datetime.fromtimestamp(spot_rows[0][0]/1000, timezone.utc).isoformat().replace('+00:00','Z'),
            coverage_end=end.isoformat().replace('+00:00','Z'), fetched_at=original_time.isoformat().replace('+00:00','Z'),
            pages=0, raw_rows=spot_rows)
        parents = {'spot4h':spot_parent, 'spot15m':None,
            'perp4h':from_futures(CandleSnapshot.model_validate(base['candles'][symbol]['trade4h'])),
            'perp15m':from_futures(CandleSnapshot.model_validate(native['candles'][symbol]['trade15m'])),
            'mark15m':from_futures(CandleSnapshot.model_validate(native['candles'][symbol]['mark15m']))}
        raw['candles'][symbol], raw['reuse_lineage'][symbol] = {}, {}
        for tag, parent in parents.items():
            interval = '4h' if tag.endswith('4h') else '15m'
            first = start if tag == 'mark15m' else start-WARMUP[interval]
            path = root/(symbol+'-'+tag+'.json')
            if reuse_root is not None:
                cache = Path(reuse_root).expanduser().resolve()/(symbol+'-'+tag+'.json')
                if cache.exists():
                    source_hashes[str(cache)] = file_hash(cache)
                    cached = FilterSnapshot.model_validate(read_inputs(cache))
                    if cached.market != ('spot' if tag.startswith('spot') else 'mark' if tag == 'mark15m' else 'perp') or cached.symbol != symbol or cached.interval != interval:
                        raise ValueError('reused supplemental cache identity mismatch')
                    if parent:
                        verify_retained(cached,parent,cached.coverage_start,end)
                    parent = cached
            if resume and path.exists():
                snap = FilterSnapshot.model_validate(read_inputs(path))
            elif tag.startswith('spot'):
                snap = extend(parent, first, end, spot_fetcher) if parent else spot_fetcher(symbol, interval, first, end)
            else:
                snap = extend(parent, first, end,
                    lambda s,i,a,z:from_futures(perp_fetcher(s,i,a,z, price_kind='trade')))
            expected_market = 'spot' if tag.startswith('spot') else 'mark' if tag == 'mark15m' else 'perp'
            if (snap.symbol, snap.market, snap.interval, snap.coverage_start, snap.coverage_end) != (
                    symbol, expected_market, interval, first, end):
                raise ValueError('supplemental snapshot identity mismatch')
            if parent:
                verify_retained(snap,parent,first,end)
            if not path.exists():
                _write(path, encoded(snap.model_dump(mode='json')))
            raw['candles'][symbol][tag] = snap.model_dump(mode='json')
            raw['reuse_lineage'][symbol][tag] = dict(parent_snapshot_id=parent.snapshot_id if parent else None,
                active_rows_reused=parent is not None, supplemental_snapshot_id=snap.snapshot_id)
            progress(f'{symbol} {tag}: {len(snap.raw_rows)} bars frozen')
        raw['funding'][symbol] = base['funding'][symbol]
    raw['bundle_checksum'] = fingerprint(raw)
    verify_files(source_hashes)
    _write(root/'inputs.json', encoded(raw))
    return root/'inputs.json'


def verify_files(hashes):
    if any(file_hash(Path(path)) != digest for path,digest in hashes.items()):
        raise ValueError('immutable source checksum changed')


def decode_bundle(config, raw):
    if raw.get('bundle_checksum') != fingerprint({k:v for k,v in raw.items() if k != 'bundle_checksum'}):
        raise ValueError('filter input bundle checksum mismatch')
    if raw.get('schema_version') != 'donchian-filters-1' or raw['window'] != {
            'start':config.start.isoformat(), 'end':config.end.isoformat()} or any(
            set(raw[k]) != set(config.weights) for k in ('candles','funding','reuse_lineage')):
        raise ValueError('filter input bundle identity mismatch')
    data, funding = {}, {}
    for s, payloads in raw['candles'].items():
        if set(payloads) != {'spot4h','spot15m','perp4h','perp15m','mark15m'}:
            raise ValueError('missing native filter series')
        data[s] = {}
        for tag, payload in payloads.items():
            snap = FilterSnapshot.model_validate(payload)
            interval = '4h' if tag.endswith('4h') else '15m'
            first = config.start if tag == 'mark15m' else config.start-WARMUP[interval]
            market = 'spot' if tag.startswith('spot') else 'mark' if tag == 'mark15m' else 'perp'
            if (snap.symbol, snap.market, snap.interval, snap.coverage_start, snap.coverage_end) != (
                    s, market, interval, first, config.end):
                raise ValueError('native filter snapshot identity mismatch')
            data[s][tag] = snap.candles()
        for market in ('spot','perp'):
            verify_boundaries(s, data[s][market+'4h'], data[s][market+'15m'], config.start, config.end, market+' H4/M15')
        history = FundingSnapshot.model_validate(raw['funding'][s]).history
        if history.symbol != s or history.coverage_start > config.start or history.coverage_end < config.end:
            raise ValueError('funding identity/coverage mismatch')
        funding[s] = history
    return data, funding


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Explicit public supplemental collection; no orders or runtime writes')
    for name in ('frozen-root','sol-inputs','output-root'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--resume', action='store_true', help='Reuse validated immutable checkpoint files')
    parser.add_argument('--reuse-root', help='Reuse a prior supplemental collection, fetching only missing prefix')
    args = parser.parse_args(argv)
    collect(args.frozen_root, args.sol_inputs, args.output_root, resume=args.resume, reuse_root=args.reuse_root)


if __name__ == '__main__':
    main()
