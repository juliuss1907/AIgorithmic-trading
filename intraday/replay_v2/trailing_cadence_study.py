"""Three paired trailing cadences on one immutable historical base dataset."""

from datetime import timedelta, timezone, datetime
import hashlib
import json
from pathlib import Path

from intraday.backups import verify_backup
from intraday.replay_v2.artifacts import SERIES, _write, publish_report, read_report
from intraday.replay_v2.contracts import Candle, FundingHistory
from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical, funding_audit
from intraday.replay_v2.historical_study import read_inputs, collect_inputs, decode_inputs
from intraday.replay_v2.metrics import encoded
from intraday.replay_v2.portfolio_study import START, END, file_hash, load_inputs
from intraday.replay_v2 import trailing_data
from intraday.store import IntradayStore


PRESET = 'perp-trailing-cadence'


def study_configs(start, end, trend_filter=True):
    return [(interval, HistoricalConfig(start=start, end=end, trend_filter=trend_filter,
        capital_growth='realized', perp_stop='atr14-3x', perp_daily_policy='none',
        perp_trade_exit='net-trailing-3pp', perp_trailing_interval=interval, drawdown_policy='observe-only'))
        for interval in ('4h', '1h', '30m')]


def run_study(database, report_root, *, start=START, end=END, inputs_path=None, collect_perp=False,
              trailing_inputs_path=None, collect_trailing=False, progress=None, loader=load_inputs,
              trend_filter=True, perp_inputs=None, trailing_inputs=None):
    variants = study_configs(start, end, trend_filter)
    cfg = variants[0][1]
    if int(collect_perp)+int(inputs_path is not None)+int(perp_inputs is not None) != 1:
        raise ValueError('choose exactly one historical Perp input source')
    if int(collect_trailing)+int(trailing_inputs_path is not None)+int(trailing_inputs is not None) != 1:
        raise ValueError('choose exactly one supplemental trailing input source')
    source = Path(database).expanduser().resolve()
    manifest, before = verify_backup(source), file_hash(source)
    spot, daily = loader(IntradayStore(source, read_only=True), cfg)
    root = Path(report_root).expanduser().resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    _write(root/'spot-inputs.json', encoded({'candles': {s: [c.row() for c in rows] for s, rows in spot.items()},
        'daily': daily, 'source_sha256': before, 'source_manifest': manifest}))
    if perp_inputs is None:
        raw = collect_inputs(cfg, root, progress=progress) if collect_perp else read_inputs(inputs_path)
        _write(root/'perp-inputs.json', encoded(raw))
        perp, funding = decode_inputs(cfg, read_inputs(root/'perp-inputs.json'))
        base_name = 'perp-inputs.json'
    else:
        perp, funding = perp_inputs
        _write(root/'fixture-perp-inputs.json', encoded({'perp': {s: {'trade': [c.row() for c in p['trade']],
            'mark': [c.row() for c in p['mark']], 'daily': p['daily']} for s, p in perp.items()},
            'funding': {s: h.model_dump(mode='json') for s,h in funding.items()}}))
        fixture = read_inputs(root/'fixture-perp-inputs.json')
        perp = {s: {'trade': tuple(Candle.from_row(r) for r in p['trade']),
            'mark': tuple(Candle.from_row(r) for r in p['mark']), 'daily': p['daily']}
            for s,p in fixture['perp'].items()}
        funding = {s: FundingHistory.model_validate(h) for s,h in fixture['funding'].items()}
        base_name = 'fixture-perp-inputs.json'
    try:
        raw = (trailing_data.collect_inputs(cfg, root, progress=progress) if collect_trailing else
               read_inputs(trailing_inputs_path) if trailing_inputs is None else trailing_inputs)
        _write(root/'trailing-inputs.json', encoded(raw))
        finer = trailing_data.decode_inputs(cfg, read_inputs(root/'trailing-inputs.json'), perp)
    except (OSError, ValueError) as error:
        _write(root/'collection-error.json', encoded({'status': 'incomplete', 'full_return_available': False,
            'error_type': type(error).__name__, 'message': str(error), 'source_unchanged': file_hash(source) == before}))
        raise
    exported = read_inputs(root/'spot-inputs.json')
    spot = {s: tuple(Candle.from_row(r) for r in rows) for s,rows in exported['candles'].items()}
    daily = exported['daily']
    hashes = {name: file_hash(root/name) for name in ('spot-inputs.json', base_name, 'trailing-inputs.json')}
    results = []
    for interval, config in variants:
        bars = None if interval == '4h' else finer[interval]
        report = simulate_historical(config, spot, daily, perp, funding, trailing_bars=bars)
        report['inputs'].update(source_sha256=before, snapshot_files_sha256=hashes)
        saved = publish_report(root/'reports', report)
        loaded = read_report(root/'reports', saved['run_id'])
        restored = HistoricalConfig.model_validate({k:v for k,v in loaded['config'].items()
                                                   if k in HistoricalConfig.model_fields})
        repeated = simulate_historical(restored, spot, daily, perp, funding, trailing_bars=bars)
        if loaded['result_id'] != repeated['result_id'] or loaded['summary'] != repeated['summary']:
            raise ValueError('trailing cadence summary does not reproduce')
        for series in SERIES:
            digest = hashlib.sha256()
            for row in repeated[series]:
                digest.update((encoded(row)+'\n').encode())
            if file_hash(Path(saved['report_directory'])/(series+'.jsonl')) != digest.hexdigest():
                raise ValueError('trailing cadence journal does not reproduce')
        trades = [t for t in repeated['trades'] if t['market'] == 'perp']
        item = {'variant': interval, 'perp_trailing_interval': interval, 'run_id': saved['run_id'],
            'result_id': report['result_id'], 'dataset_checksum': report['inputs']['dataset_checksum'],
            'base_dataset_checksum': report['inputs'].get('base_dataset_checksum', report['inputs']['dataset_checksum']),
            'summary': report['summary'], 'status': report['status'], 'deterministic_rerun_verified': True,
            'mean_perp_holding_hours': float(sum(t['holding_hours'] for t in trades)/len(trades)) if trades else None}
        results.append(item)
        if progress:
            progress({'phase': 'trailing_cadence_replay_complete', **item})
    if len({r['base_dataset_checksum'] for r in results}) != 1 or len({r['result_id'] for r in results}) != 3:
        raise ValueError('cadence comparison requires one base dataset and three unique results')
    if file_hash(source) != before or any(file_hash(root/name) != h for name,h in hashes.items()):
        raise ValueError('immutable cadence evidence changed')
    reference = results[0]['summary']
    for row in results:
        row['return_difference_vs_h4_pp'] = (row['summary']['net_return_pct']-reference['net_return_pct']
            if row['summary']['net_return_pct'] is not None and reference['net_return_pct'] is not None else None)
        row['final_equity_difference_vs_h4'] = row['summary']['final_equity_known']-reference['final_equity_known']
    receipt = {'preset': PRESET, 'research_only': True, 'activation_allowed': False, 'official_gate_eligible': False,
        'window': {'start': start.isoformat(), 'end': end.isoformat()}, 'original_source': str(source),
        'source_manifest': manifest, 'source_sha256': before, 'source_unchanged': True,
        'snapshot_files_sha256': hashes, 'funding_audit': funding_audit(cfg, funding),
        'supplemental_bundle_checksum': raw['bundle_checksum'], 'results': results}
    _write(root/'comparison.json', json.dumps(receipt, indent=2, allow_nan=False))
    _write(root/'comparison.md', comparison_markdown(receipt))
    return receipt


def comparison_markdown(receipt):
    local = timezone(timedelta(hours=7))
    lines = ['# Trailing cadence comparison', '',
        'Research only; not Jev/LLM execution, independent holdout or an activation gate.', '',
        'Window UTC+7: '+' → '.join(datetime.fromisoformat(receipt['window'][k]).astimezone(local).isoformat()
            for k in ('start','end')),
        '1,000 USDT; separate realized sizing: Spot 600, Perp notional 300, reserve 100; isolated 3x.',
        'Only contract trailing cadence changes. H4 entries/ATR14 x3/Donchian 30/8 and daily/funding risk unchanged.',
        'Net per-trade return on entry notional: arm +3%, continuous peak minus 3 percentage points.',
        'Close breach latches until next native open, even on rebound. Open gaps exit at open; high/low never arm trailing.',
        'No fixed daily profit cap; Perp daily loss -3%. Peak DD observe-only; research check still requires DD <10%.', '',
        '| Trailing | Final USDT | Return | Spot PnL | Perp PnL | Observed DD | Perp trades | Trail exits | Mean hold hours | Delta vs H4 USDT | Check |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for row in receipt['results']:
        s, p = row['summary'], row['summary']['perp_daily']
        spot = sum(v['net_pnl'] for k,v in s['contributions'].items() if k.startswith('spot:'))
        net = f"{s['net_return_pct']:.4f}%" if s['net_return_pct'] is not None else 'UNKNOWN'
        hold = row['mean_perp_holding_hours']
        lines.append(f"| {row['variant']} | {s['final_equity_known']:.4f} | {net} | {spot:.4f} | "
            f"{p['pnl_after_known_costs']:.4f} | {s['max_drawdown_known_pct']:.4f}% | {p['closed_trades']} | "
            f"{s['perp_trade_trailing']['exit_count']} | {hold if hold is not None else '—'} | "
            f"{row['final_equity_difference_vs_h4']:.4f} | {s['economic_check_only']} |")
    lines += ['', 'H4 preserves the original evaluator identity. All cases share the frozen base checksum; finer contract evidence is hashed separately.',
        'Native H1 and M30 collected independently, not inferred from H4; all common H4 open/close prices verified.',
        'Fine trailing fills add cost checks using last H4/funding marks; no fine Spot/mark feed or periodic risk checks.',
        'ATR touch detection remains H4-close; actual intrabar risk DD/stop ordering unknown. Not real-time trailing or Binance Demo parity.',
        'Full summary and all three journal series reproduced offline. Inputs/source unchanged. No promotion, push or deployment.', '']
    return '\n'.join(lines)
