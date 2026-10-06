"""Three paired local intraday experiments with frozen offline verification."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from intraday.backups import verify_backup
from intraday.replay_v2.artifacts import SERIES, _write, publish_report, read_report
from intraday.replay_v2.contracts import Candle, FundingHistory
from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical
from intraday.replay_v2.historical_study import decode_inputs, read_inputs
from intraday.replay_v2.intraday_book import IntradayConfig
from intraday.replay_v2.intraday_engine import simulate_intraday
from intraday.replay_v2.intraday_audit import audit_drawdown
from intraday.replay_v2 import intraday_data
from intraday.replay_v2.metrics import encoded, journal_hash
from intraday.replay_v2.portfolio_study import END, file_hash, load_inputs
from intraday.store import IntradayStore


PRESET = 'perp-intraday-timeframes'
START = datetime(2024, 10, 29, tzinfo=timezone.utc)


def study_configs(start, end, trend_filter=True):
    control = HistoricalConfig(start=start, end=end, trend_filter=trend_filter,
        capital_growth='realized', perp_stop='atr14-3x', perp_daily_policy='none',
        perp_trade_exit='net-trailing-3pp', perp_trailing_interval='1h', drawdown_policy='observe-only')
    return [('A-H4-D1-trailH1', control)] + [(name, IntradayConfig(start=start, end=end,
        trend_filter=trend_filter, perp_trailing_interval=interval)) for name,interval in
        [('B-H1-H4H8-trailH1','1h'), ('C-H1-H4H8-trailM15','15m')]]


def verify_reference(directory, report):
    directory = Path(directory).expanduser().resolve()
    reference = read_report(directory.parent, directory.name)
    if reference['result_id'] != report['result_id'] or reference['summary'] != report['summary']:
        raise ValueError('original H4/D1/H1 control identity or summary changed')
    for series in SERIES:
        if file_hash(directory/(series+'.jsonl')) != journal_hash(report[series]):
            raise ValueError('original control full journal changed: '+series)


def replay(config, spot, daily, perp, funding, native):
    if isinstance(config, IntradayConfig):
        return simulate_intraday(config, spot, daily, perp, funding, native)
    fine = {s:tuple(b for b in p['trade1h'] if config.start <= b.opened_at < config.end)
            for s,p in native.items()}
    return simulate_historical(config, spot, daily, perp, funding, trailing_bars=fine)


def frozen_base(config, root, path, fixtures):
    if fixtures is None:
        raw = read_inputs(path)
        name = 'perp-inputs.json'
        _write(root/name, encoded(raw))
        return (*decode_inputs(config, read_inputs(root/name)), name)
    perp, funding = fixtures
    name = 'fixture-perp-inputs.json'
    _write(root/name, encoded({'perp':{s:{'trade':[c.row() for c in p['trade']],
        'mark':[c.row() for c in p['mark']], 'daily':p['daily']} for s,p in perp.items()},
        'funding':{s:h.model_dump(mode='json') for s,h in funding.items()}}))
    raw = read_inputs(root/name)
    return ({s:{'trade':tuple(Candle.from_row(r) for r in p['trade']),
                'mark':tuple(Candle.from_row(r) for r in p['mark']), 'daily':p['daily']}
             for s,p in raw['perp'].items()},
            {s:FundingHistory.model_validate(h) for s,h in raw['funding'].items()}, name)


def run_study(database, report_root, *, start=START, end=END, inputs_path=None,
              intraday_inputs_path=None, collect_intraday=False, progress=None,
              reuse_perp_path=None, reuse_trailing_path=None, baseline_reference=None,
              loader=load_inputs, trend_filter=True, perp_inputs=None, intraday_inputs=None):
    if int(inputs_path is not None)+int(perp_inputs is not None) != 1:
        raise ValueError('intraday study requires one frozen base Perp input source')
    if int(collect_intraday)+int(intraday_inputs_path is not None)+int(intraday_inputs is not None) != 1:
        raise ValueError('choose exactly one collect-intraday / intraday-inputs source')
    if (reuse_perp_path or reuse_trailing_path) and not collect_intraday:
        raise ValueError('reuse collection paths require collect-intraday')
    variants = study_configs(start, end, trend_filter)
    cfg = variants[0][1]
    source = Path(database).expanduser().resolve()
    manifest, before = verify_backup(source), file_hash(source)
    spot, daily = loader(IntradayStore(source, read_only=True), cfg)
    root = Path(report_root).expanduser().resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    _write(root/'spot-inputs.json', encoded({'candles':{s:[c.row() for c in rows] for s,rows in spot.items()},
        'daily':daily, 'source_sha256':before, 'source_manifest':manifest}))
    perp, funding, base_name = frozen_base(cfg, root, inputs_path, perp_inputs)
    try:
        if collect_intraday:
            reused, files = intraday_data.reusable_history(reuse_perp_path, reuse_trailing_path)
            raw = intraday_data.collect_inputs(cfg, root, reused=reused, reused_files=files, progress=progress)
        else:
            raw = read_inputs(intraday_inputs_path) if intraday_inputs is None else intraday_inputs
        _write(root/'intraday-inputs.json', encoded(raw))
        native = intraday_data.decode_inputs(cfg, read_inputs(root/'intraday-inputs.json'), perp)
    except (OSError, ValueError) as error:
        _write(root/'collection-error.json', encoded(dict(status='incomplete', full_return_available=False,
            error_type=type(error).__name__, message=str(error), source_unchanged=file_hash(source) == before)))
        raise
    spot_raw = read_inputs(root/'spot-inputs.json')
    spot = {s:tuple(Candle.from_row(r) for r in rows) for s,rows in spot_raw['candles'].items()}
    daily = spot_raw['daily']
    hashes = {name:file_hash(root/name) for name in ('spot-inputs.json', base_name, 'intraday-inputs.json')}
    results = []
    for index, (name, config) in enumerate(variants):
        if progress:
            progress(dict(phase='intraday_replay_started', variant=name))
        report = replay(config, spot, daily, perp, funding, native)
        if index == 0 and baseline_reference:
            verify_reference(baseline_reference, report)
        report['inputs'].update(source_sha256=before, snapshot_files_sha256=hashes)
        saved = publish_report(root/'reports', report)
        loaded = read_report(root/'reports', saved['run_id'])
        cls = IntradayConfig if index else HistoricalConfig
        restored = cls.model_validate({k:v for k,v in loaded['config'].items() if k in cls.model_fields})
        repeated = replay(restored, spot, daily, perp, funding, native)
        if loaded['result_id'] != repeated['result_id'] or loaded['summary'] != repeated['summary']:
            raise ValueError('intraday summary does not reproduce')
        for series in SERIES:
            if file_hash(Path(saved['report_directory'])/(series+'.jsonl')) != journal_hash(repeated[series]):
                raise ValueError('intraday full journal does not reproduce: '+series)
        sidecar = None
        if index:
            sidecar = name+'-perp-equity.jsonl'
            _write(root/sidecar, ''.join(encoded(r)+'\n' for r in report['perp_equity_curve']))
            if file_hash(root/sidecar) != journal_hash(repeated['perp_equity_curve']):
                raise ValueError('fine Perp risk curve does not reproduce')
        audit, curve = audit_drawdown(report, spot, native)
        second_audit, second_curve = audit_drawdown(repeated, spot, native)
        if audit != second_audit or journal_hash(curve) != journal_hash(second_curve):
            raise ValueError('passive common-grid audit does not reproduce')
        audit_name = name+'-audit.jsonl'
        _write(root/audit_name, ''.join(encoded(r)+'\n' for r in curve))
        if file_hash(root/audit_name) != journal_hash(curve):
            raise ValueError('common-grid audit publication mismatch')
        trades = [t for t in report['trades'] if t['market'] == 'perp']
        item = dict(variant=name, run_id=saved['run_id'], result_id=report['result_id'],
            base_dataset_checksum=report['inputs']['base_dataset_checksum'], summary=report['summary'],
            status=report['status'], deterministic_rerun_verified=True, common_grid_audit=audit,
            audit_file=audit_name, audit_sha256=file_hash(root/audit_name),
            perp_curve_file=sidecar, perp_curve_sha256=file_hash(root/sidecar) if sidecar else None,
            mean_perp_holding_hours=float(sum(t['holding_hours'] for t in trades)/len(trades)) if trades else None)
        results.append(item)
        if progress:
            progress(dict(phase='intraday_replay_complete', **item))
    if len({r['base_dataset_checksum'] for r in results}) != 1 or len({r['result_id'] for r in results}) != 3:
        raise ValueError('intraday matrix requires one frozen base and three distinct identities')
    if file_hash(source) != before or any(file_hash(root/name) != h for name,h in hashes.items()):
        raise ValueError('immutable intraday evidence changed')
    reference = results[0]['summary']
    for row in results:
        row['final_equity_difference_vs_A'] = row['summary']['final_equity_known']-reference['final_equity_known']
        row['return_difference_vs_A_pp'] = (row['summary']['net_return_pct']-reference['net_return_pct']
            if row['summary']['net_return_pct'] is not None and reference['net_return_pct'] is not None else None)
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
    lines = ['# Perp intraday timeframe comparison', '',
        'Research only; no Jev/LLM, holdout, gate approval or activation.', '',
        'Window UTC+7: '+' → '.join(datetime.fromisoformat(receipt['window'][k]).astimezone(local).isoformat()
            for k in ('start','end')),
        '1,000 USDT; Spot600 / Perp notional300 / reserve100; isolated3x; separate realized sizing.',
        'A: unchanged H4/D1 entries and risk, H1 trailing. B/C: H1 entries, H4+H8 EMA50 consensus, ATR14 H1 x3, M15 Perp risk/stop detection.',
        'B trails at H1 closes; C at M15 closes. Net arm3%, peak-3pp; next own-interval open, latched close breach.',
        'Parent risk remains H4 plus funding/cost events for ALL cases; Spot rules and H4/D1 cadence unchanged.',
        'Perp daily loss3%; no fixed daily profit target. DD observe-only; economic check still DD<10%.', '',
        '| Case | Final USDT | Return | Spot PnL | Perp PnL | Engine DD | Common M15 DD | Perp DD engine / audit | Perp trades | Trail exits | Daily Perp halts | Mean hold h | Delta vs A |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in receipt['results']:
        s, p, audit = row['summary'], row['summary']['perp_daily'], row['common_grid_audit']
        spot = sum(v['net_pnl'] for k,v in s['contributions'].items() if k.startswith('spot:'))
        net = f"{s['net_return_pct']:.4f}%" if s['net_return_pct'] is not None else 'UNKNOWN'
        hold = row['mean_perp_holding_hours']
        lines.append(f"| {row['variant']} | {s['final_equity_known']:.4f} | {net} | {spot:.4f} | "
            f"{p['pnl_after_known_costs']:.4f} | {s['max_drawdown_known_pct']:.4f}% | {audit['max_drawdown_pct']:.4f}% | "
            f"{p['max_drawdown_known_pct']:.4f}% / {audit['perp_max_drawdown_pct']:.4f}% | {p['closed_trades']} | "
            f"{s['perp_trade_trailing']['exit_count']} | {p['loss_halts']} | {hold if hold is not None else '—'} | "
            f"{row['final_equity_difference_vs_A']:.4f} |")
    lines += ['', 'Engine DD uses its execution clock; the passive common-grid audit uses identical native M15 Perp marks and H4 Spot as-of prices.',
        'Audit cannot feed back into trades, guards, original A identity or summary. Intrabar Spot DD and OHLC stop order remain unknown.',
        'Shared cash and parent halts can change Spot fills despite identical Spot rules. Closing costs/gaps can overshoot limits.',
        'Full summary, three journals, new Perp risk sidecars and audit reproduced offline; source and frozen inputs unchanged.',
        'Research is ex-post, not current Jev/confidence/Demo execution; no automatic winner/promotion.', '']
    return '\n'.join(lines)
