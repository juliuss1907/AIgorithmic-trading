"""Frozen native intraday inputs, explicit reuse lineage and strict boundaries."""

from datetime import timedelta

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.historical_data import CandleSnapshot, fetch_candle_snapshot, validate_rows
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


SERIES = {
    'trade1h': ('1h', 'trade', timedelta(hours=31)),
    'trade4h': ('4h', 'trade', timedelta(hours=240)),
    'trade8h': ('8h', 'trade', timedelta(hours=480)),
    'trade15m': ('15m', 'trade', timedelta()),
    'mark15m': ('15m', 'mark', timedelta()),
}


def reusable_history(perp_path=None, trailing_path=None):
    reused, files = {}, {}
    for path, schema, tag, key in ((perp_path, '1', 'trade4h', 'trade4h'),
                                  (trailing_path, 'trailing-1', 'trade1h', '1h')):
        if path is None:
            continue
        raw = read_inputs(path)
        if raw.get('schema_version') != schema or raw.get('bundle_checksum') != fingerprint(
            {k:v for k,v in raw.items() if k != 'bundle_checksum'}):
            raise ValueError('reused native history bundle identity or checksum mismatch')
        files[str(path)] = file_hash(path)
        for s, payloads in raw['candles'].items():
            snapshot = CandleSnapshot.model_validate(payloads[key])
            if snapshot.symbol != s or snapshot.price_kind != 'trade' or snapshot.interval != SERIES[tag][0]:
                raise ValueError('reused native snapshot identity mismatch')
            reused[(s, tag)] = snapshot
    return reused, files


def subset_snapshot(snapshot, start, end):
    if not snapshot.coverage_start <= start < end <= snapshot.coverage_end:
        raise ValueError('reused native snapshot does not cover warmup and window')
    raw = snapshot.model_dump(mode='json', exclude={'snapshot_id'})
    first, last = int(start.timestamp()*1000), int(end.timestamp()*1000)
    raw.update(coverage_start=start.isoformat().replace('+00:00', 'Z'),
               coverage_end=end.isoformat().replace('+00:00', 'Z'),
               raw_rows=[row for row in snapshot.raw_rows if first <= row[0] < last])
    # Original pages/fetched_at describe the parent acquisition, not a new request.
    return CandleSnapshot(snapshot_id=fingerprint(raw), **raw)


def collect_inputs(config, root, *, candle_fetcher=fetch_candle_snapshot, progress=None,
                   reused=None, reused_files=None):
    reused = reused or {}
    raw = {'schema_version': 'intraday-1', 'window': {
        'start': config.start.isoformat(), 'end': config.end.isoformat()},
        'candles': {}, 'reuse_lineage': {}, 'reused_files_sha256': reused_files or {}}
    directory = root/'raw-intraday'
    directory.mkdir(mode=0o700)
    for s in sorted(config.perp_weights):
        raw['candles'][s], raw['reuse_lineage'][s] = {}, {}
        for tag, (interval, kind, warmup) in SERIES.items():
            parent = reused.get((s, tag))
            snapshot = (subset_snapshot(parent, config.start-warmup, config.end) if parent else
                        candle_fetcher(s, interval, config.start-warmup, config.end, price_kind=kind))
            if parent:
                raw['reuse_lineage'][s][tag] = dict(parent_snapshot_id=parent.snapshot_id,
                    parent_coverage_start=parent.coverage_start.isoformat(),
                    parent_coverage_end=parent.coverage_end.isoformat(),
                    unchanged_row_subset=True, acquisition_metadata_retained=True)
            payload = snapshot.model_dump(mode='json')
            _write(directory/(s+'-'+tag+'.json'), encoded(payload))
            raw['candles'][s][tag] = payload
            if progress:
                progress(dict(phase='native_intraday_ready', symbol=s, series=tag,
                    bars=len(snapshot.raw_rows), snapshot_id=snapshot.snapshot_id, reused=bool(parent)))
    return {**raw, 'bundle_checksum': fingerprint(raw)}


def verify_boundaries(symbol, large, small, start, end, label):
    opens = {c.opened_at:c.open for c in small}
    closes = {c.available_at:c.close for c in small}
    for bar in large:
        if start <= bar.opened_at < end and (
            opens.get(bar.opened_at) != bar.open or closes.get(bar.available_at) != bar.close):
            raise ValueError(f'{symbol}: {label} boundary prices differ at {bar.opened_at.isoformat()}')


def validate_inputs(config, data, perp):
    if set(data) != set(config.perp_weights):
        raise ValueError('intraday symbols mismatch')
    for s, series in data.items():
        if set(series) != set(SERIES):
            raise ValueError('intraday native series missing or unexpected')
        for tag, (interval, _, warmup) in SERIES.items():
            rows = series[tag]
            if any(getattr(c, 'interval', '4h') != interval for c in rows):
                raise ValueError('intraday interval mismatch')
            validate_rows([c.row() for c in rows], interval, config.start-warmup, config.end)
        for tag in ('trade1h', 'trade4h', 'trade8h'):
            verify_boundaries(s, series[tag], series['trade15m'], config.start, config.end, tag+'/M15')
        verify_boundaries(s, perp[s]['trade'], series['trade15m'], config.start, config.end, 'frozen H4/M15')
        verify_boundaries(s, perp[s]['mark'], series['mark15m'], config.start, config.end, 'frozen mark H4/M15')
    return data


def decode_inputs(config, raw, perp):
    if not isinstance(raw, dict) or raw.get('bundle_checksum') != fingerprint(
        {k:v for k,v in raw.items() if k != 'bundle_checksum'}):
        raise ValueError('intraday bundle checksum mismatch')
    if set(raw) != {'schema_version', 'window', 'candles', 'reuse_lineage', 'reused_files_sha256', 'bundle_checksum'} or (
        raw['schema_version'] != 'intraday-1' or raw['window'] != {
            'start': config.start.isoformat(), 'end': config.end.isoformat()}
        or set(raw['candles']) != set(config.perp_weights)):
        raise ValueError('intraday bundle identity mismatch')
    decoded = {}
    for s, series in sorted(raw['candles'].items()):
        if set(series) != set(SERIES):
            raise ValueError('intraday native series missing or unexpected')
        decoded[s] = {}
        for tag, payload in series.items():
            interval, kind, warmup = SERIES[tag]
            snap = CandleSnapshot.model_validate(payload)
            if (snap.symbol, snap.interval, snap.price_kind, snap.coverage_start, snap.coverage_end) != (
                s, interval, kind, config.start-warmup, config.end):
                raise ValueError('intraday snapshot identity or warmup mismatch')
            decoded[s][tag] = snap.candles()
    return validate_inputs(config, decoded, perp)
