"""Build a private G4-shaped research copy; only public candles/funding may be fetched.

Source database, frozen bundles and their manifests are read-only. No provider profiles
or secret stores are read. Each collected snapshot is retained for exact reuse.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intraday import active_set, setup2, setup2_store
from intraday.replay_v2.donchian_five_data import read_manifest
from intraday.replay_v2.donchian_filter_data import fetch_spot_snapshot, verify_files
from intraday.replay_v2.funding import fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from intraday.replay_v2.metrics import encoded
from intraday.replay_v2.portfolio_study import file_hash
from intraday.store import IntradayStore


def retained(path, fetch):
    if path.exists():
        return json.loads(path.read_text())
    result = fetch().model_dump(mode='json')
    with path.open('x') as stream:
        stream.write(encoded(result)+'\n')
    return result


def prepare(source, inputs, root, end):
    root.mkdir(parents=True, exist_ok=True)
    binding = dict(source_database=str(source), source_sha256=file_hash(source), inputs=str(inputs),
                   inputs_sha256=file_hash(inputs), end=end.isoformat(), research_only=True)
    bound = root/'binding.json'
    if bound.exists():
        if json.loads(bound.read_text()) != binding:
            raise ValueError('research input binding changed')
    else:
        with bound.open('x') as stream:
            stream.write(encoded(binding)+'\n')
    target = root/'research.sqlite3'
    if not target.exists():
        print('Copying local G5 snapshot into private research database', flush=True)
        with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    store = IntradayStore(target)
    setup2_store.install(store)
    version = active_set.current(store)
    if version is None or set(version.plan.coins) != {'ETHUSDT', 'NEARUSDT', 'SOLUSDT', 'ZECUSDT', 'HYPEUSDT'}:
        raise ValueError('expected exact five-coin ADR-007 active-set copy')
    print('Verifying retained public bundle lineage', flush=True)
    raw = read_manifest(inputs)
    lineage = raw['source_files_sha256']
    verify_files(lineage)
    manifest = dict(binding=binding, original_sources_sha256=lineage, collected={}, routes=[])
    for symbol, coin in sorted(version.plan.coins.items()):
        settings = setup2_store.coin_settings(store, symbol)
        for market in ('spot', 'perp'):
            for interval in ('4h', '15m'):
                start = coin.indicator_anchor if interval == '4h' else setup2_store.profile_start(
                    coin.indicator_anchor, coin.warmup_bars)
                if market == 'spot' and settings.proxy_until:
                    start = settings.proxy_until
                    if interval == '15m':
                        start = setup2_store.first_spot_bar(symbol, interval, start)
                tag = market+interval
                payload = raw.get('candles', {}).get(symbol, {}).get(tag)
                rows = []
                if payload:
                    rows = [r for r in payload['raw_rows']
                            if int(start.timestamp()*1000) <= r[0] and r[6] < int(end.timestamp()*1000)]
                cursor = datetime.fromtimestamp((int(rows[-1][6])+1)/1000, timezone.utc) if rows else start
                if cursor < end:
                    path = root/f'{symbol}-{tag}-public.json'
                    print(f'Public {symbol} {tag}: {cursor.isoformat()} -> {end.isoformat()}', flush=True)
                    snap = retained(path, lambda: fetch_spot_snapshot(symbol, interval, cursor, end)
                                    if market == 'spot' else fetch_candle_snapshot(symbol, interval, cursor, end))
                    if datetime.fromisoformat(snap['coverage_start'].replace('Z', '+00:00')) != cursor or (
                        datetime.fromisoformat(snap['coverage_end'].replace('Z', '+00:00')) != end):
                        raise ValueError('retained public snapshot window mismatch')
                    rows.extend(snap['raw_rows'])
                    manifest['collected'][str(path)] = file_hash(path)
                bars = setup2.bars(rows, setup2_store.WIDTHS[interval])
                if not bars or bars[0].opened_at != start or bars[-1].available_at != end or not setup2.contiguous(
                    bars, setup2_store.WIDTHS[interval]):
                    raise ValueError(f'incomplete native history: {symbol} {tag}')
                # Existing helper verifies immutability against any checkpoint rows.
                setup2_store.record_candles(store, symbol, market, interval, rows)
                print(f'Stored {symbol} {tag}: {len(rows)} native bars', flush=True)
            manifest['routes'].append(dict(symbol=symbol, market=market, anchor=coin.indicator_anchor.isoformat(),
                                          warmup_bars=coin.warmup_bars))
        path = root/f'{symbol}-funding-public.json'
        print(f'Public {symbol} funding: whole anchored window', flush=True)
        retained(path, lambda: fetch_funding_snapshot(symbol, coin.indicator_anchor, end))
        manifest['collected'][str(path)] = file_hash(path)
    verify_files(lineage)
    assert file_hash(source) == binding['source_sha256'], 'source database changed'
    with sqlite3.connect(target) as c:
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    manifest['research_database_sha256'] = file_hash(target)
    path = root/'prepared.json'
    text = encoded(manifest)+'\n'
    if path.exists():
        if path.read_text() != text:
            raise ValueError('completed preparation is immutable')
    else:
        with path.open('x') as stream:
            stream.write(text)
    print('Prepared:', path, 'sha256:', file_hash(path), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--end', type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    prepare(args.source_database.resolve(), args.inputs.resolve(), args.output_root.resolve(), args.end)
