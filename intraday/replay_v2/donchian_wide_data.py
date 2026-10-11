"""Wide-basket acquisition from public Binance data, strict and one-shot, research only.

Nothing is repaired: a coin whose history has a gap, a native H4/M15 disagreement or incomplete
funding is dropped and reported, and the next ranked coin takes its place. The ranking uses the
current list, so the basket carries survivorship bias; the manifest states it.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_filter_data import (
    WARMUP, FilterSnapshot, fetch_spot_snapshot, from_futures)
from intraday.replay_v2.donchian_hourly import h4_reproduction
from intraday.replay_v2.funding import NoRedirect, FundingSnapshot, fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.intraday_data import verify_boundaries
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash

SCHEMA = 'donchian-wide-manifest-1'
FUT_INFO = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
FUT_TICKER = 'https://fapi.binance.com/fapi/v1/ticker/24hr'
FUT_KLINES = 'https://fapi.binance.com/fapi/v1/klines'
SPOT_INFO = 'https://api.binance.com/api/v3/exchangeInfo'
SPOT_KLINES = 'https://api.binance.com/api/v3/klines'
METHOD = ('USDT-M perpetuals TRADING with underlying COIN, onboarded before the warmup start, with a TRADING Spot '
          'market of the same ticker listed before the warmup start, stablecoin and 1000x contracts excluded, '
          'ranked by 90 daily USDT quote volumes; the current list is used (survivorship bias).')
STABLE_BASES = frozenset({'USDC', 'FDUSD', 'TUSD', 'BUSD', 'USDP', 'DAI', 'USDE', 'USD1', 'PYUSD', 'EUR', 'AEUR',
                          'XUSD', 'RLUSD', 'USDS', 'UST', 'USTC'})
TAGS = ('spot4h', 'spot15m', 'perp4h', 'perp15m', 'mark15m')
FETCH_ERRORS = (ValueError, OSError, HTTPError, URLError, KeyError, TypeError)


def public_get(url, query=None):
    request = Request(url+('?'+urlencode(query) if query else ''), headers={'User-Agent': 'aigt-wide-research/1'})
    for attempt in range(3):
        try:
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                body = response.read(40_000_001)
            if len(body) > 40_000_000:
                raise ValueError('public response exceeds size limit')
            return json.loads(body)
        except HTTPError as error:
            if attempt == 2 or error.code not in {418, 429, 500, 502, 503, 504}:
                raise
            time.sleep(min(30, max(1, int(error.headers.get('Retry-After', 2)))))
        except URLError:
            if attempt == 2:
                raise
            time.sleep(attempt+1)


def floor_h4(at):
    return at.replace(hour=at.hour//4*4, minute=0, second=0, microsecond=0)


def select_universe(get=public_get, *, warmup_start, now, preselect=100, keep=45, pace=0.05):
    """Ranked, availability-checked candidates plus every exclusion with its reason."""
    warmup_ms = int(warmup_start.timestamp()*1000)
    excluded, eligible = [], []
    for item in get(FUT_INFO)['symbols']:
        symbol = item.get('symbol', '')
        reason = (None if re.fullmatch(r'[A-Z][A-Z0-9]{1,19}USDT', symbol) else 'symbol_form_or_multiplier'
                  if re.match(r'\d', symbol) else 'symbol_form')
        reason = reason or (
            'not_perpetual_trading' if item.get('contractType') != 'PERPETUAL' or item.get('status') != 'TRADING'
            else 'not_usdt_margined' if item.get('quoteAsset') != 'USDT'
            else 'not_coin_underlying' if item.get('underlyingType', 'COIN') != 'COIN'
            else 'stablecoin' if item.get('baseAsset') in STABLE_BASES
            else 'perp_onboarded_after_warmup' if int(item.get('onboardDate', 0)) > warmup_ms else None)
        if reason:
            if reason != 'not_perpetual_trading':  # Delisted/settling contracts are noise in the report.
                excluded.append(dict(symbol=symbol, stage='perp_metadata', reason=reason))
            continue
        eligible.append(symbol)
    volume_24h = {row['symbol']: float(row['quoteVolume']) for row in get(FUT_TICKER)}
    shortlist = sorted(eligible, key=lambda s: (-volume_24h.get(s, 0.0), s))[:preselect]
    end_ms = int(now.timestamp()*1000)
    ranked = []
    for symbol in shortlist:
        time.sleep(pace)
        try:
            rows = get(FUT_KLINES, dict(symbol=symbol, interval='1d', limit=90, endTime=end_ms))
            ranked.append((sum(float(r[7]) for r in rows), symbol, len(rows)))
        except FETCH_ERRORS as error:
            excluded.append(dict(symbol=symbol, stage='volume', reason=f'volume_unavailable: {error}'))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    candidates = []
    for volume, symbol, days in ranked:
        if len(candidates) >= keep:
            break
        time.sleep(pace)
        try:
            info = get(SPOT_INFO, dict(symbol=symbol))['symbols'][0]
            if info.get('status') != 'TRADING':
                excluded.append(dict(symbol=symbol, stage='spot', reason='spot_not_trading'))
                continue
            first = get(SPOT_KLINES, dict(symbol=symbol, interval='1d', limit=1, startTime=0))
            if not first or int(first[0][0]) > warmup_ms:
                excluded.append(dict(symbol=symbol, stage='spot', reason='spot_history_starts_after_warmup'))
                continue
        except FETCH_ERRORS:
            excluded.append(dict(symbol=symbol, stage='spot', reason='no_spot_market'))
            continue
        candidates.append(dict(symbol=symbol, rank=len(candidates)+1, quote_volume_90d=volume, days=days))
    return dict(as_of=now.isoformat(), method=METHOD, warmup_start=warmup_start.isoformat(),
                preselected=len(shortlist), candidates=candidates, excluded=excluded)


def _funding_complete(snapshot, start, end):
    times = [row.at for row in snapshot.history.settlements if start <= row.at < end]
    gaps = [(b-a).total_seconds() for a, b in zip(times, times[1:])]
    if not (times and times[0]-start <= timedelta(hours=8, seconds=1) and end-times[-1] <= timedelta(hours=8, seconds=1)
            and max(gaps, default=0) <= 8*3600+1):
        raise ValueError('funding history is incomplete')


def fetch_coin(symbol, start, end, *, spot_fetch=fetch_spot_snapshot, perp_fetch=fetch_candle_snapshot,
               funding_fetch=fetch_funding_snapshot):
    """All six series for one coin, validated against each other; raises ValueError on any weakness."""
    first = {'4h': start-WARMUP['4h'], '15m': start-WARMUP['15m']}
    snaps = {
        'spot4h': spot_fetch(symbol, '4h', first['4h'], end),
        'spot15m': spot_fetch(symbol, '15m', first['15m'], end),
        'perp4h': from_futures(perp_fetch(symbol, '4h', first['4h'], end, price_kind='trade')),
        'perp15m': from_futures(perp_fetch(symbol, '15m', first['15m'], end, price_kind='trade')),
        'mark15m': from_futures(perp_fetch(symbol, '15m', start, end, price_kind='mark'))}
    funding = funding_fetch(symbol, start, end)
    _funding_complete(funding, start, end)
    for market in ('spot', 'perp'):
        large, small = snaps[market+'4h'].candles(), snaps[market+'15m'].candles()
        verify_boundaries(symbol, large, small, start, end, market+' H4/M15')
        if h4_reproduction(large, small)['disagreements']:
            raise ValueError(f'{symbol} {market}: M15 aggregation disagrees with native H4')
    return snaps, funding


def collect(root, universe, start, end, *, want=30, progress=print, **fetchers):
    """Collect candidates in rank order until `want` coins validate; write snapshots once and a manifest."""
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    files, accepted, rejected = {}, [], []
    for candidate in universe['candidates']:
        if len(accepted) >= want:
            break
        symbol = candidate['symbol']
        try:
            snaps, funding = fetch_coin(symbol, start, end, **fetchers)
        except FETCH_ERRORS as error:
            rejected.append(dict(symbol=symbol, stage='collection', reason=str(error)[:300]))
            progress(f'excluded {symbol}: {error}')
            continue
        files[symbol] = {}
        for tag, snap in {**snaps, 'funding': funding}.items():
            path = root/f'{symbol}-{tag}.json'
            _write(path, encoded(snap.model_dump(mode='json')))
            files[symbol][tag] = dict(path=str(path), sha256=file_hash(path),
                                      snapshot_id=getattr(snap, 'snapshot_id', None) or snap.funding_id)
        accepted.append(symbol)
        progress(f'accepted {symbol} ({len(accepted)}/{want})')
    if len(accepted) < want:
        raise ValueError(f'only {len(accepted)} of {want} coins validated; see {root}')
    manifest = dict(schema_version=SCHEMA, window=dict(start=start.isoformat(), end=end.isoformat()),
                    symbols=sorted(accepted), rank_order=accepted, files=files, method=METHOD,
                    universe=universe, rejected=rejected,
                    created_at=datetime.now(timezone.utc).isoformat())
    manifest['manifest_checksum'] = fingerprint(manifest)
    _write(root/'manifest.json', encoded(manifest))
    return root/'manifest.json'


def read_wide_manifest(path):
    """Verified manifest -> the `donchian-filters-1` bundle shape that `decode_bundle` reads."""
    manifest = read_inputs(path)
    if (manifest.get('schema_version') != SCHEMA or manifest.get('manifest_checksum') !=
            fingerprint({k: v for k, v in manifest.items() if k != 'manifest_checksum'})):
        raise ValueError('wide manifest checksum/schema mismatch')
    raw = dict(schema_version='donchian-filters-1', window=manifest['window'], source_files_sha256={},
               candles={}, funding={}, reuse_lineage={})
    for symbol in manifest['symbols']:
        raw['candles'][symbol], raw['reuse_lineage'][symbol] = {}, {}
        for tag, ref in manifest['files'][symbol].items():
            if file_hash(ref['path']) != ref['sha256']:
                raise ValueError('immutable wide snapshot changed')
            raw['source_files_sha256'][ref['path']] = ref['sha256']
            payload = read_inputs(ref['path'])
            if tag == 'funding':
                raw['funding'][symbol] = payload
            else:
                raw['candles'][symbol][tag] = payload
                raw['reuse_lineage'][symbol][tag] = dict(snapshot_id=ref['snapshot_id'], source=payload['source'])
    raw['source_files_sha256'][str(Path(path).resolve())] = file_hash(path)
    raw['bundle_checksum'] = fingerprint({k: v for k, v in raw.items() if k != 'bundle_checksum'})
    return raw


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Explicit public collection of the wide basket; no orders, no models')
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--start', default='2026-01-01T00:00:00+00:00')
    parser.add_argument('--end', help='aligned H4 boundary; default: latest completed H4 boundary')
    parser.add_argument('--want', type=int, default=30)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end) if args.end else floor_h4(now)-timedelta(hours=4)
    universe = select_universe(warmup_start=start-WARMUP['4h'], now=now)
    print(f"{len(universe['candidates'])} candidates, {len(universe['excluded'])} excluded before collection")
    print(collect(args.output_root, universe, start, end, want=args.want))


if __name__ == '__main__':
    main()
