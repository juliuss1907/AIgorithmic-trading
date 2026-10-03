"""Supplemental native contract history; never replaces frozen H4 evidence."""

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.historical_data import CandleSnapshot, fetch_candle_snapshot, validate_rows
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.trailing_candle import WIDTHS


def validate_inputs(config, bars, perp):
    interval = config.perp_trailing_interval
    if interval not in WIDTHS or set(bars) != set(config.perp_weights):
        raise ValueError('trailing interval or symbols mismatch')
    for s, rows in sorted(bars.items()):
        if any(c.interval != interval for c in rows):
            raise ValueError('trailing candle interval mismatch')
        validate_rows([c.row() for c in rows], interval, config.start, config.end)
        by_open = {c.opened_at: c for c in rows}
        by_close = {c.available_at: c for c in rows}
        for bar in perp[s]['trade']:
            if config.start <= bar.opened_at < config.end and (
                by_open[bar.opened_at].open != bar.open or by_close[bar.available_at].close != bar.close):
                raise ValueError(s+': trailing and frozen H4 boundary prices differ')
    return bars


def collect_inputs(config, root, *, candle_fetcher=fetch_candle_snapshot, progress=None):
    raw = {'schema_version': 'trailing-1', 'window': {
        'start': config.start.isoformat(), 'end': config.end.isoformat()}, 'candles': {}}
    directory = root/'raw-trailing'
    directory.mkdir(mode=0o700)
    for s in sorted(config.perp_weights):
        raw['candles'][s] = {}
        for interval in WIDTHS:
            snapshot = candle_fetcher(s, interval, config.start, config.end, price_kind='trade')
            payload = snapshot.model_dump(mode='json')
            _write(directory/(s+'-'+interval+'.json'), encoded(payload))
            raw['candles'][s][interval] = payload
            if progress:
                progress({'phase': 'collected_native_trailing', 'symbol': s, 'interval': interval,
                          'bars': len(snapshot.raw_rows), 'snapshot_id': snapshot.snapshot_id})
    return {**raw, 'bundle_checksum': fingerprint(raw)}


def decode_inputs(config, raw, perp):
    if not isinstance(raw, dict) or raw.get('bundle_checksum') != fingerprint(
        {k: v for k, v in raw.items() if k != 'bundle_checksum'}):
        raise ValueError('trailing bundle checksum mismatch')
    if set(raw) != {'schema_version', 'window', 'candles', 'bundle_checksum'} or (
        raw['schema_version'] != 'trailing-1' or raw['window'] != {
            'start': config.start.isoformat(), 'end': config.end.isoformat()}
        or set(raw['candles']) != set(config.perp_weights)):
        raise ValueError('trailing bundle identity mismatch')
    decoded = {interval: {} for interval in WIDTHS}
    for s, data in sorted(raw['candles'].items()):
        if set(data) != set(WIDTHS):
            raise ValueError('trailing native series missing or unexpected')
        for interval, payload in data.items():
            snapshot = CandleSnapshot.model_validate(payload)
            if (snapshot.symbol, snapshot.interval, snapshot.price_kind,
                snapshot.coverage_start, snapshot.coverage_end) != (
                s, interval, 'trade', config.start, config.end):
                raise ValueError('trailing series identity or coverage mismatch')
            decoded[interval][s] = snapshot.candles()
    for interval, bars in decoded.items():
        validate_inputs(config.model_copy(update={'perp_trailing_interval': interval}), bars, perp)
    return decoded
