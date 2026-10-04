"""Twenty opt-in offline ablations with immutable reports and full journal replay."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from intraday.replay_v2.artifacts import _write, publish_report, read_report, SERIES
from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.donchian_filter_data import decode_bundle, verify_files
from intraday.replay_v2.donchian_filter_engine import prepare, simulate, PAIRS, VERSION
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.intraday_study import journal_hash
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


def study_configs(start,end):
    return [(f'A{level}-Donchian{entry}-{exit}',FilterConfig(start=start,end=end,
            entry_window=entry,exit_window=exit,filter_level=level))
            for level in range(5) for entry,exit in PAIRS]


def run_study(inputs_path, report_root, *, progress=print):
    source = Path(inputs_path).expanduser().resolve()
    raw, before = read_inputs(source), file_hash(source)
    start,end = (datetime.fromisoformat(raw['window'][key]) for key in ('start','end'))
    variants = study_configs(start,end)
    verify_files(raw['source_files_sha256'])
    data,funding = decode_bundle(variants[0][1],raw)
    root = Path(report_root).expanduser().resolve()
    root.mkdir(parents=True,mode=0o700,exist_ok=False)
    _write(root/'inputs.json',encoded(raw))
    frozen = read_inputs(root/'inputs.json')
    data,funding = decode_bundle(variants[0][1],frozen)
    del raw,frozen
    progress('Preparing causal H4 features and prior20-day M15 profiles')
    prepared = prepare(variants[0][1],data,funding)
    del data,funding
    results = []
    for name,config in variants:
        progress('replay '+name)
        report = simulate(config,prepared)
        saved = publish_report(root/'reports',report)
        restored = FilterConfig.model_validate({k:v for k,v in report['config'].items() if k in FilterConfig.model_fields})
        repeated = simulate(restored,prepared)
        loaded = read_report(root/'reports',saved['run_id'])
        if loaded['result_id'] != repeated['result_id'] or loaded['summary'] != repeated['summary']:
            raise ValueError('filter summary does not reproduce')
        for series in SERIES:
            if file_hash(Path(saved['report_directory'])/(series+'.jsonl')) != journal_hash(repeated[series]):
                raise ValueError('filter full journal does not reproduce: '+series)
        if fingerprint(report['methodology']) != fingerprint(repeated['methodology']):
            raise ValueError('filter methodology does not reproduce')
        item = dict(variant=name,run_id=saved['run_id'],result_id=report['result_id'],
            dataset_checksum=prepared.checksum,status=report['status'],summary=report['summary'],
            deterministic_rerun_verified=True,report_directory=saved['report_directory'])
        results.append(item)
        progress(encoded(dict(variant=name,final_usdt=report['summary']['final_equity_known'],
            return_pct=report['summary']['net_return_pct'],dd_pct=report['summary']['max_drawdown_known_pct'],
            closed_trades=report['summary']['closed_trades'])))
        del report,repeated,loaded
    lineage = read_inputs(source)['source_files_sha256']
    verify_files(lineage)
    if file_hash(source) != before or file_hash(root/'inputs.json') != before:
        raise ValueError('immutable filter evidence changed')
    receipt = dict(preset='donchian-filter-study',evaluator_version=VERSION,
        research_only=True,activation_allowed=False,official_gate_eligible=False,
        window=dict(start=start.isoformat(),end=end.isoformat()),
        inputs_path=str(source),inputs_sha256=before,source_unchanged=True,
        source_files_sha256=lineage,results=results)
    _write(root/'comparison.json',encoded(receipt))
    _write(root/'comparison.md',comparison_markdown(receipt))
    return receipt


def comparison_markdown(receipt):
    local = ZoneInfo('Asia/Ho_Chi_Minh')
    start,end = (datetime.fromisoformat(receipt['window'][k]).astimezone(local).isoformat() for k in ('start','end'))
    lines = ['# Donchian Spot + Short1x filter comparison','',
        f'Window UTC+7: {start} → {end}. Initial1,000USDT; Spot60% / Short40%.',
        'Both sleeves BTC40% ETH30% SOL30%; own realized reinvestment and legacy ATR sizing.',
        'Parent daily loss3% UTC; ATR14x3 H4 ratcheting trailing checkedM15; Donchian exit retained; DDobserve-only.',
        'A0 Donchian; A1 +EMA200/50; A2 +ADX/DMI14; A3 +VolumeMA20>1.2; A4 +prior20-dayM15 VA70% profile.',
        'Each level includes all four20/8,20/10,30/8,30/10 pairs; same clocks/costs/capital.',
        'Profile is approximate, not tick volume-at-price; old windows already viewed, not untouched out-of-sample.',
        'Research only; not official gate eligibility, activation or deployment. Old studies unchanged.','',
        '| Case | Final USDT | Net PnL | Return | M15 DD | Trades | Daily stops |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for item in receipt['results']:
        s = item['summary']
        net = f"{s['net_return_pct']:.4f}%" if s['net_return_pct'] is not None else 'UNKNOWN'
        lines.append(f"| {item['variant']} | {s['final_equity_known']:.4f} | {s['pnl_after_known_costs']:.4f} | "
            f"{net} | {s['max_drawdown_known_pct']:.4f}% | {s['closed_trades']} | {s['daily_stops']} |")
    lines += ['', 'Each report includes per-coin/market gross/net/fees/slippage/funding, holding times,',
              'all filter rejection counts, continuous six-month segments and full replay-verified journals.',
              'Funding is actual settlement history. Costs Spot10/5bps and Perp5/5bps per fill.',
              'No daily profit target, reserve transfer or unrealized sizing; exact intrabar path/liquidation unknown.', '']
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Offline Donchian20/30 entry x8/10 exit,20case Spot/Short1x ablation')
    parser.add_argument('--inputs',required=True)
    parser.add_argument('--report-root',required=True)
    args = parser.parse_args(argv)
    run_study(args.inputs,args.report_root)


if __name__ == '__main__':
    main()
