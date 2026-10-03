"""Explicit 24-month historical study and immutable offline reproduction."""

from datetime import timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

from intraday.backups import verify_backup
from intraday.replay_v2.artifacts import SERIES, _write, publish_report, read_report
from intraday.replay_v2.contracts import Candle, FundingHistory
from intraday.replay_v2.funding import FundingSnapshot, fetch_funding_snapshot
from intraday.replay_v2.historical_data import CandleSnapshot, fetch_candle_snapshot
from intraday.replay_v2.historical_mixed import HistoricalConfig, funding_audit, simulate_historical
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_research import validate_inputs
from intraday.replay_v2.portfolio_study import START, END, file_hash, load_inputs
from intraday.store import IntradayStore


def study_configs(start, end, trend_filter=True):
    variants = [('Spot+Perp', HistoricalConfig(start=start, end=end, trend_filter=trend_filter,
        perp_stop=stop, daily_loss=daily)) for stop in ('fixed-1pct', 'atr14-2x') for daily in ('.03', '.05')]
    return variants+ [('Spot-only control', HistoricalConfig(start=start, end=end, trend_filter=trend_filter,
        include_perp=False, daily_loss=daily)) for daily in ('.03', '.05')]


def read_inputs(path):
    fd = os.open(Path(path).expanduser().resolve(), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        body = stream.read(200_000_001)
    if len(body) > 200_000_000:
        raise ValueError('historical input bundle exceeds size limit')
    return json.loads(body)


def collect_inputs(config, root, *, candle_fetcher=fetch_candle_snapshot,
                   funding_fetcher=fetch_funding_snapshot, progress=None):
    raw = {'schema_version': '1', 'window': {'start': config.start.isoformat(), 'end': config.end.isoformat()},
           'candles': {}, 'funding': {}}
    directory = root/'raw-futures'
    directory.mkdir(mode=0o700)
    for s in sorted(config.perp_weights):
        raw['candles'][s] = {}
        for tag, interval, kind, warmup in (('trade4h', '4h', 'trade', timedelta(hours=124)),
            ('trade1d', '1d', 'trade', timedelta(days=60)), ('mark4h', '4h', 'mark', timedelta(0))):
            snapshot = candle_fetcher(s, interval, config.start-warmup, config.end, price_kind=kind)
            payload = snapshot.model_dump(mode='json')
            _write(directory/(s+'-'+tag+'.json'), encoded(payload))
            raw['candles'][s][tag] = payload
            if progress:
                progress({'phase': 'collected_native_futures', 'symbol': s, 'series': tag,
                          'bars': len(snapshot.raw_rows), 'snapshot_id': snapshot.snapshot_id})
        snapshot = funding_fetcher(s, config.start, config.end)
        raw['funding'][s] = snapshot.model_dump(mode='json')
        _write(directory/(s+'-funding.json'), encoded(raw['funding'][s]))
        if progress:
            progress({'phase': 'collected_funding', 'symbol': s, 'settlements': len(snapshot.raw_rows),
                      'funding_id': snapshot.funding_id})
    return {**raw, 'bundle_checksum': fingerprint(raw)}


def decode_inputs(config, raw):
    if not isinstance(raw, dict):
        raise ValueError('historical Futures input bundle must be an object')
    if raw.get('bundle_checksum') != fingerprint({k: v for k, v in raw.items() if k != 'bundle_checksum'}):
        raise ValueError('historical Futures input bundle checksum mismatch')
    if raw.get('schema_version') != '1' or raw.get('window') != {
        'start': config.start.isoformat(), 'end': config.end.isoformat()} or any(
        set(raw.get(k, {})) != set(config.perp_weights) for k in ('candles', 'funding')):
        raise ValueError('historical Futures bundle window or symbols mismatch')
    perp, funding = {}, {}
    for s, data in sorted(raw['candles'].items()):
        if set(data) != {'trade4h', 'trade1d', 'mark4h'}:
            raise ValueError('historical Futures series missing or unexpected')
        validated = {}
        for tag, interval, kind, warmup in (('trade4h', '4h', 'trade', timedelta(hours=124)),
            ('trade1d', '1d', 'trade', timedelta(days=60)), ('mark4h', '4h', 'mark', timedelta(0))):
            snapshot = CandleSnapshot.model_validate(data[tag])
            if (snapshot.symbol, snapshot.interval, snapshot.price_kind, snapshot.coverage_start, snapshot.coverage_end) != (
                s, interval, kind, config.start-warmup, config.end):
                raise ValueError('historical Futures series identity or coverage mismatch')
            validated[tag] = snapshot
        perp[s] = {'trade': validated['trade4h'].candles(), 'mark': validated['mark4h'].candles(),
                   'daily': validated['trade1d'].raw_rows}
        snapshot = FundingSnapshot.model_validate(raw['funding'][s])
        if snapshot.history.symbol != s:
            raise ValueError('historical funding symbol mismatch')
        funding[s] = snapshot.history
    if not all(p['complete'] for p in funding_audit(config, funding).values()):
        raise ValueError('historical funding coverage incomplete; no full-return study published')
    return perp, funding


def comparison_markdown(receipt):
    local = timezone(timedelta(hours=7))
    from datetime import datetime
    lines = ['# Historical Spot + quantitative Perp — 24-month research', '',
        'Research only. Not Jev/confidence replay, not an independent holdout or an activation gate.', '',
        'Window UTC+7: '+ ' → '.join(datetime.fromisoformat(receipt['window'][k]).astimezone(local).isoformat()
            for k in ('start', 'end')),
        'Capital 1,000 USDT; Spot cap 60%, Perp NOTIONAL cap 30% (BTC/ETH 15% each), isolated 3x, reserve 10%.',
        'Spot weights BTC/ETH/SOL/NEAR/ZEC = 40/20/20/10/10. Both markets Donchian 30/8 + native daily EMA50.',
        'Perp has mirrored long/short signals; fixed sizing. Stop ATR is fixed at entry, not trailing.', '',
        '| Portfolio | Perp stop | Daily loss | Net return | Observed DD | Spot trades | Perp trades | Research check |',
        '|---|---|---:|---:|---:|---:|---:|---|']
    for row in receipt['results']:
        s = row['summary']
        net = f"{s['net_return_pct']:.4f}%" if s['net_return_pct'] is not None else 'UNKNOWN'
        counts = {m: sum(v['closed_trades'] for k, v in s['contributions'].items() if k.startswith(m+':'))
                  for m in ('spot', 'perp')}
        lines.append(f"| {row['variant']} | {row['perp_stop'] if row['include_perp'] else '—'} | "
            f"{row['daily_loss_pct']:g}% | {net} | {s['max_drawdown_known_pct']:.4f}% | "
            f"{counts['spot']} | {counts['perp']} | {s['economic_check_only']} |")
    lines += ['', '## Paired comparison', '',
        '| Perp stop | Daily loss | Mixed minus Spot-control return, percentage points |', '|---|---:|---:|']
    for row in receipt['results']:
        if not row['include_perp']:
            continue
        delta = row['paired_spot_control_return_difference_pp']
        lines.append(f"| {row['perp_stop']} | {row['daily_loss_pct']:g}% | {delta if delta is not None else 'UNKNOWN'} |")
    lines += ['', '## Reading the results', '',
        'Daily loss 3%/5%, DD 10%. DD terminal halt does not reset; daily halt resumes only next UTC day AND flat.',
        'Compare to the paired 60% Spot control, not the earlier 65% Spot study.',
        'Fee/slippage per fill: Spot 10/5 bps; Perp 5/5 bps. Actual historical funding is separate.',
        'Native contract-price OHLC stops are detected at candle close; exact intrabar stop/funding timing and DD are unknown.',
        'Mark-price values positions; it is not an invented bid/ask fill. No exact liquidation or Demo execution parity.',
        'Full journals include per-coin/market and long/short contributions, costs, funding, margin/exposure, halts and four continuous six-month periods.',
        'All published ledger series were reproduced offline; the original immutable SQLite source was unchanged.', '']
    return '\n'.join(lines)


def run_study(database, report_root, *, start=START, end=END, collect_perp=False, inputs_path=None,
              loader=load_inputs, perp_inputs=None, trend_filter=True, progress=None):
    variants = study_configs(start, end, trend_filter)
    config = variants[0][1]
    if collect_perp and (inputs_path is not None or perp_inputs is not None):
        raise ValueError('choose collection OR immutable offline inputs')
    if not collect_perp and inputs_path is None and perp_inputs is None:
        raise ValueError('historical study requires --collect-perp or --perp-inputs')
    source = Path(database).expanduser().resolve()
    manifest = verify_backup(source)
    before = file_hash(source)
    spot, daily = loader(IntradayStore(source, read_only=True), config)
    validate_inputs(config, spot, daily)
    root = Path(report_root).expanduser().resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    # Export only research inputs instead of duplicating a large runtime archive.
    _write(root/'spot-inputs.json', encoded({'candles': {s: [c.row() for c in rows] for s, rows in spot.items()},
        'daily': daily, 'source_sha256': before, 'source_manifest': manifest}))
    try:
        if perp_inputs is None:
            raw = collect_inputs(config, root, progress=progress) if collect_perp else read_inputs(inputs_path)
            _write(root/'perp-inputs.json', encoded(raw))
            perp, funding = decode_inputs(config, read_inputs(root/'perp-inputs.json'))
        else:
            perp, funding = perp_inputs
            _write(root/'fixture-perp-inputs.json', encoded({'perp': {s: {'trade': [c.row() for c in p['trade']],
                'mark': [c.row() for c in p['mark']], 'daily': p['daily']} for s, p in perp.items()},
                'funding': {s: h.model_dump(mode='json') for s, h in funding.items()}}))
    except (OSError, ValueError) as error:
        _write(root/'collection-error.json', encoded({'status': 'incomplete', 'full_return_available': False,
            'error_type': type(error).__name__, 'message': str(error), 'source_unchanged': file_hash(source) == before}))
        raise
    exported = read_inputs(root/'spot-inputs.json')
    frozen_spot = {s: tuple(Candle.from_row(row) for row in rows) for s, rows in exported['candles'].items()}
    frozen_daily = exported['daily']
    if perp_inputs is not None:
        fixture = read_inputs(root/'fixture-perp-inputs.json')
        perp = {s: {'trade': tuple(Candle.from_row(row) for row in p['trade']),
            'mark': tuple(Candle.from_row(row) for row in p['mark']), 'daily': p['daily']}
            for s, p in fixture['perp'].items()}
        funding = {s: FundingHistory.model_validate(h) for s, h in fixture['funding'].items()}
    input_names = ['spot-inputs.json', 'fixture-perp-inputs.json' if perp_inputs is not None else 'perp-inputs.json']
    hashes = {name: file_hash(root/name) for name in input_names}
    results = []
    for name, cfg in variants:
        report = simulate_historical(cfg, frozen_spot, frozen_daily, perp, funding)
        report['inputs'].update(source_sha256=before, snapshot_files_sha256=hashes)
        saved = publish_report(root/'reports', report)
        loaded = read_report(root/'reports', saved['run_id'])
        restored = HistoricalConfig.model_validate({k: loaded['config'][k] for k in HistoricalConfig.model_fields})
        repeated = simulate_historical(restored, frozen_spot, frozen_daily, perp, funding)
        if loaded['result_id'] != repeated['result_id'] or loaded['summary'] != repeated['summary']:
            raise ValueError('historical published summary does not reproduce')
        for series in SERIES:
            digest = hashlib.sha256()
            for row in repeated[series]:
                digest.update((encoded(row)+'\n').encode())
            if file_hash(Path(saved['report_directory'])/(series+'.jsonl')) != digest.hexdigest():
                raise ValueError('historical published ledger does not reproduce')
        item = dict(variant=name, include_perp=cfg.include_perp, perp_stop=cfg.perp_stop,
            daily_loss_pct=float(cfg.daily_loss*100), max_drawdown_pct=float(cfg.max_drawdown*100),
            run_id=saved['run_id'], result_id=report['result_id'], dataset_checksum=report['inputs']['dataset_checksum'],
            summary=report['summary'], status=report['status'], deterministic_rerun_verified=True)
        results.append(item)
        if progress:
            progress({'phase': 'historical_replay_complete', 'variant': name, 'perp_stop': cfg.perp_stop,
                'daily_loss_pct': item['daily_loss_pct'], 'run_id': saved['run_id'],
                **{key: report['summary'][key] for key in
                   ('net_return_pct', 'max_drawdown_known_pct', 'closed_trades', 'economic_check_only')},
                'deterministic_rerun_verified': True})
    if file_hash(source) != before or any(file_hash(root/name) != value for name, value in hashes.items()):
        raise ValueError('immutable historical inputs changed')
    if len({r['result_id'] for r in results}) != 6 or len({r['dataset_checksum'] for r in results}) != 1:
        raise ValueError('historical study needs six distinct configs on one dataset')
    controls = {r['daily_loss_pct']: r['summary']['net_return_pct'] for r in results if not r['include_perp']}
    for row in results:
        mixed, control = row['summary']['net_return_pct'], controls[row['daily_loss_pct']]
        row['paired_spot_control_return_difference_pp'] = mixed-control if row['include_perp'] and mixed is not None and control is not None else None
    receipt = dict(research_only=True, activation_allowed=False, official_gate_eligible=False,
        signal_mode='historical_deterministic', window={'start': start.isoformat(), 'end': end.isoformat()},
        original_source=str(source), source_manifest=manifest, source_sha256=before, source_unchanged=True,
        snapshot_files_sha256=hashes, bars_per_coin=int((end-start)/timedelta(hours=4)),
        funding_audit=funding_audit(config, funding), results=results)
    _write(root/'comparison.json', json.dumps(receipt, indent=2, allow_nan=False))
    _write(root/'comparison.md', comparison_markdown(receipt))
    return receipt
