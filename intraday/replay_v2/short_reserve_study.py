"""Four frozen paired cases; reserve policy never changes deployed execution."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from intraday.backups import verify_backup
from intraday.store import IntradayStore
from intraday.replay_v2.artifacts import SERIES, _write, publish_report, read_report
from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.intraday_audit import audit_drawdown
from intraday.replay_v2.intraday_data import decode_inputs
from intraday.replay_v2.intraday_study import (START, frozen_base, journal_hash, replay as old_replay,
                                             study_configs as old_configs, verify_reference)
from intraday.replay_v2.metrics import encoded
from intraday.replay_v2.portfolio_study import END, file_hash, load_inputs
from intraday.replay_v2.short_reserve_book import ShortReserveConfig
from intraday.replay_v2.short_reserve_engine import simulate_short_reserve


PRESET = 'perp-short-reserve'


def study_configs(start, end, trend_filter=True):
    control = old_configs(start, end, trend_filter)[0][1]
    spot = HistoricalConfig(start=start, end=end, trend_filter=trend_filter, include_perp=False,
        capital_growth='realized', drawdown_policy='observe-only')
    return [('Spot-only', spot), ('A-H4-D1-trailH1', control)] + [(name,
        ShortReserveConfig(start=start, end=end, trend_filter=trend_filter, reserve_policy=policy))
        for name,policy in [('Short2x-reserve-off','off'), ('Short2x-restore-repay','restore-and-repay')]]


def replay(config, spot, daily, perp, funding, native):
    if isinstance(config, ShortReserveConfig):
        return simulate_short_reserve(config, spot, daily, perp, funding, native)
    if config.include_perp:
        return old_replay(config, spot, daily, perp, funding, native)
    return simulate_historical(config, spot, daily, perp, funding)


def publish_verified(root, name, report, repeated, spot, native):
    saved = publish_report(root/'reports', report)
    loaded = read_report(root/'reports', saved['run_id'])
    if loaded['result_id'] != repeated['result_id'] or loaded['summary'] != repeated['summary']:
        raise ValueError('short-reserve summary does not reproduce')
    for series in SERIES:
        if file_hash(Path(saved['report_directory'])/(series+'.jsonl')) != journal_hash(repeated[series]):
            raise ValueError('full journal does not reproduce: '+series)
    sidecars = {}
    for series in ('perp_equity_curve', 'reserve_transfers'):
        if series not in report:
            continue
        path = name+'-'+series+'.jsonl'
        _write(root/path, ''.join(encoded(row)+'\n' for row in report[series]))
        if file_hash(root/path) != journal_hash(repeated[series]):
            raise ValueError('short reserve sidecar does not reproduce: '+series)
        sidecars[series] = dict(file=path, sha256=file_hash(root/path))
    audit, curve = audit_drawdown(report, spot, native)
    second, second_curve = audit_drawdown(repeated, spot, native)
    if audit != second or journal_hash(curve) != journal_hash(second_curve):
        raise ValueError('short reserve passive audit does not reproduce')
    path = name+'-audit.jsonl'
    _write(root/path, ''.join(encoded(row)+'\n' for row in curve))
    sidecars['common_grid_audit'] = dict(file=path, sha256=file_hash(root/path))
    return dict(variant=name, run_id=saved['run_id'], result_id=report['result_id'],
        base_dataset_checksum=report['inputs'].get('base_dataset_checksum', report['inputs']['dataset_checksum']),
        summary=report['summary'], status=report['status'], deterministic_rerun_verified=True,
        common_grid_audit=audit, sidecars=sidecars)


def run_study(database, report_root, *, start=START, end=END, inputs_path=None,
              intraday_inputs_path=None, baseline_reference=None, progress=None,
              loader=load_inputs, trend_filter=True, perp_inputs=None, intraday_inputs=None):
    if (int(inputs_path is not None)+int(perp_inputs is not None) != 1 or
            int(intraday_inputs_path is not None)+int(intraday_inputs is not None) != 1):
        raise ValueError('short-reserve requires frozen base and native inputs only')
    variants = study_configs(start, end, trend_filter)
    cfg = variants[1][1]
    source = Path(database).expanduser().resolve()
    manifest, before = verify_backup(source), file_hash(source)
    spot, daily = loader(IntradayStore(source, read_only=True), cfg)
    root = Path(report_root).expanduser().resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    _write(root/'spot-inputs.json', encoded(dict(candles={s:[c.row() for c in rows] for s,rows in spot.items()},
        daily=daily, source_sha256=before, source_manifest=manifest)))
    perp, funding, base_name = frozen_base(cfg, root, inputs_path, perp_inputs)
    raw = read_inputs(intraday_inputs_path) if intraday_inputs is None else intraday_inputs
    _write(root/'intraday-inputs.json', encoded(raw))
    native = decode_inputs(cfg, read_inputs(root/'intraday-inputs.json'), perp)
    frozen = read_inputs(root/'spot-inputs.json')
    spot = {s:tuple(Candle.from_row(r) for r in rows) for s,rows in frozen['candles'].items()}
    daily = frozen['daily']
    hashes = {name:file_hash(root/name) for name in ('spot-inputs.json',base_name,'intraday-inputs.json')}
    results = []
    for name, config in variants:
        if progress:
            progress(dict(phase='short_reserve_replay_started', variant=name))
        report = replay(config, spot, daily, perp, funding, native)
        if name == 'A-H4-D1-trailH1' and baseline_reference:
            verify_reference(baseline_reference, report)
        report['inputs'].update(source_sha256=before, snapshot_files_sha256=hashes)
        cls = type(config)
        restored = cls.model_validate({k:v for k,v in report['config'].items() if k in cls.model_fields})
        repeated = replay(restored, spot, daily, perp, funding, native)
        item = publish_verified(root, name, report, repeated, spot, native)
        results.append(item)
        if progress:
            progress(dict(phase='short_reserve_replay_complete', **item))
    if len({r['result_id'] for r in results}) != 4 or len({r['base_dataset_checksum'] for r in results}) != 1:
        raise ValueError('short-reserve requires four distinct configs on one frozen base')
    if file_hash(source) != before or any(file_hash(root/name) != value for name,value in hashes.items()):
        raise ValueError('immutable short-reserve evidence changed')
    receipt = dict(preset=PRESET, research_only=True, activation_allowed=False, official_gate_eligible=False,
        window=dict(start=start.isoformat(), end=end.isoformat()), original_source=str(source),
        source_manifest=manifest, source_sha256=before, source_unchanged=True,
        baseline_reference_verified=bool(baseline_reference), snapshot_files_sha256=hashes,
        supplemental_bundle_checksum=raw['bundle_checksum'], results=results)
    _write(root/'comparison.json', json.dumps(receipt, indent=2, allow_nan=False))
    _write(root/'comparison.md', comparison_markdown(receipt))
    return receipt


def comparison_markdown(receipt):
    local = timezone(timedelta(hours=7))
    duration = (datetime.fromisoformat(receipt['window']['end'])-
                datetime.fromisoformat(receipt['window']['start'])).total_seconds()/86400
    lines = ['# Short-only Perp and reserve comparison', '',
        'Research only; no LLM calls, gate promotion or trading activation.', '',
        'Window UTC+7: '+' → '.join(datetime.fromisoformat(receipt['window'][k]).astimezone(local).isoformat()
            for k in ('start','end')),
        'Initial1,000USDT; Spot600 own realized; new Perp300 MARGIN, short-only isolated2x; reserve100.',
        'Original A retains300 notional and3x, H1 trailing. New cases use H4/D1 Donchian30/8, ATR14 sizing,10% price stop,M15 Perp risk, no trailing.',
        'Daily loss3% own Perp equity and parent; DD10% observe-only, economic check unchanged.',
        'Flat-only draw restores Perp300; surplus repays reserve100 first. Transfers are NOT trading PnL.', '',
        '| Case | Final USDT | Return | Spot PnL | Perp PnL | Engine DD | M15 DD | Perp trades | Perp daily halts | Draw / repay | Reserve | Perp capital |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in receipt['results']:
        s, a = row['summary'], row['common_grid_audit']
        spot = sum(v['net_pnl'] for k,v in s['contributions'].items() if k.startswith('spot:'))
        perp = sum(v['net_pnl'] for k,v in s['contributions'].items() if k.startswith('perp:'))
        d, r = s.get('perp_daily',{}), s.get('reserve',{})
        net = f"{s['net_return_pct']:.4f}%" if s['net_return_pct'] is not None else 'UNKNOWN'
        lines.append(f"| {row['variant']} | {s['final_equity_known']:.4f} | {net} | {spot:.4f} | {perp:.4f} | "
            f"{s['max_drawdown_known_pct']:.4f}% | {a['max_drawdown_pct']:.4f}% | "
            f"{sum(v['closed_trades'] for k,v in s['contributions'].items() if k.startswith('perp:'))} | "
            f"{d.get('loss_halts',0)} | {r.get('drawn',0):.4f} / {r.get('repaid',0):.4f} | "
            f"{s['realized_sizing']['final_capital']['unallocated']:.4f} | "
            f"{s['realized_sizing']['final_capital']['perp']:.4f} |")
    lines += ['', f'All cases use one frozen {duration:g}-day base; common M15 audit is passive, Spot H4 as-of.',
        'Perp performance DD excludes capital flows; actual funded equity and reserve are separately recorded.',
        'Cumulative draws can exceed100 through repayments; remaining reserve is always0..100.',
        'No transfer into live positions, no Spot subsidy, no window-end replenishment. Fees, slippage and actual funding charged once.',
        'Same-timestamp flat-batch entry cooldown applies to both new cases. Source and exports remain unchanged.',
        'Full summaries, three journals, reserve/fine-equity sidecars and common-grid audits reproduce offline.', '']
    return '\n'.join(lines)
