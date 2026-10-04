"""Frozen BTC/ETH plus separately acquired SOL; explicit offline-only replay."""

from datetime import datetime
from pathlib import Path

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.historical_study import read_inputs, decode_inputs
from intraday.replay_v2.intraday_data import decode_inputs as decode_native
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash
from intraday.replay_v2.short_reserve_study import publish_verified
from intraday.replay_v2.single_sleeve_research import (
    WEIGHTS, PerpOnlyConfig, SpotOnlyConfig, simulate_single_sleeve)


def run_study(frozen_root, sol_inputs, report_root, *, progress=print):
    source, sol = Path(frozen_root).resolve(), Path(sol_inputs).resolve()
    prior = read_inputs(source/'comparison.json')
    if prior.get('preset') != 'perp-short-reserve':
        raise ValueError('single-sleeve requires the canonical frozen short-reserve study')
    inherited = {source/name:checksum for name,checksum in prior['snapshot_files_sha256'].items()}
    inherited[source/'comparison.json'] = file_hash(source/'comparison.json')
    database = Path(prior['original_source'])
    inherited[database] = prior['source_sha256']
    inherited.update({sol/name:file_hash(sol/name) for name in ('perp-inputs.json','intraday-inputs.json')})
    if any(file_hash(path) != value for path,value in inherited.items()):
        raise ValueError('immutable input/source checksum changed')
    start, end = (datetime.fromisoformat(prior['window'][k]) for k in ('start','end'))
    cfg = PerpOnlyConfig(start=start, end=end)
    raw_spot = read_inputs(source/'spot-inputs.json')
    spot = {s:tuple(Candle.from_row(row) for row in raw_spot['candles'][s]) for s in WEIGHTS}
    daily = {s:raw_spot['daily'][s] for s in WEIGHTS}
    combined = []
    for name in ('perp-inputs.json','intraday-inputs.json'):
        old, extra = read_inputs(source/name), read_inputs(sol/name)
        if (set(old['candles']) != {'BTCUSDT','ETHUSDT'} or set(extra['candles']) != {'SOLUSDT'} or
                old['window'] != prior['window'] or extra['window'] != prior['window'] or
                any(raw['bundle_checksum'] != fingerprint({k:v for k,v in raw.items() if k != 'bundle_checksum'})
                    for raw in (old, extra))):
            raise ValueError('incompatible or corrupted SOL supplemental bundle')
        merged = {k:v for k,v in old.items() if k != 'bundle_checksum'}
        for key in ('candles','funding','reuse_lineage','reused_files_sha256'):
            if key in merged:
                merged[key] = {**old[key], **extra[key]}
        if 'reused_files_sha256' in merged:
            merged['reused_files_sha256'].update({str(source/name):file_hash(source/name),
                                                  str(sol/name):file_hash(sol/name)})
        combined.append({**merged, 'bundle_checksum':fingerprint(merged)})
    perp, funding = decode_inputs(cfg, combined[0])
    native = decode_native(cfg, combined[1], perp)
    root = Path(report_root).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write(root/'spot-inputs.json', encoded({**raw_spot,
        'candles':{s:[c.row() for c in rows] for s,rows in spot.items()}, 'daily':daily}))
    for name, raw in zip(('perp-inputs.json','intraday-inputs.json'), combined):
        _write(root/name, encoded(raw))
    # Replay from the published exports, never from an acquisition response.
    raw_spot = read_inputs(root/'spot-inputs.json')
    spot = {s:tuple(Candle.from_row(row) for row in rows) for s,rows in raw_spot['candles'].items()}
    daily = raw_spot['daily']
    perp, funding = decode_inputs(cfg, read_inputs(root/'perp-inputs.json'))
    native = decode_native(cfg, read_inputs(root/'intraday-inputs.json'), perp)
    hashes = {name:file_hash(root/name) for name in ('spot-inputs.json','perp-inputs.json','intraday-inputs.json')}
    results = []
    for name, config in [('Spot-only-Donchian100', SpotOnlyConfig(start=start, end=end)),
                         ('Perp-only-Intraday3x-trailM15-100margin', cfg)]:
        progress('replay '+name)
        report = simulate_single_sleeve(config, spot, daily, perp, funding, native)
        restored = type(config).model_validate({k:v for k,v in report['config'].items() if k in type(config).model_fields})
        repeated = simulate_single_sleeve(restored, spot, daily, perp, funding, native)
        report['inputs'].update(source_sha256=prior['source_sha256'], snapshot_files_sha256=hashes)
        result = publish_verified(root, name, report, repeated, spot, native)
        results.append(result)
        progress(encoded(dict(variant=name, summary=result['summary'])))
    if (any(file_hash(path) != value for path,value in inherited.items()) or
            any(file_hash(root/name) != value for name,value in hashes.items())):
        raise ValueError('immutable single-sleeve evidence changed')
    receipt = dict(preset='single-sleeve-full-capital', research_only=True, activation_allowed=False,
        official_gate_eligible=False, window=prior['window'], original_source=str(database),
        source_sha256=prior['source_sha256'], source_unchanged=True,
        input_lineage_sha256={str(path):value for path,value in inherited.items()},
        snapshot_files_sha256=hashes, results=results)
    _write(root/'comparison.json', encoded(receipt))
    _write(root/'comparison.md', comparison_markdown(receipt))
    return receipt


def comparison_markdown(receipt):
    days = (datetime.fromisoformat(receipt['window']['end'])-
            datetime.fromisoformat(receipt['window']['start'])).days
    lines = ['# BTC/ETH/SOL full-capital comparison', '',
        f'Research only; {days} days; initial1,000USDT each; BTC/ETH/SOL50/25/25.',
        'Spot Donchian30/8 H4/D1, ATR14 sizing,10% emergency price stop.',
        'Perp long/short isolated3x, H1 Donchian30/8, H4+H8 EMA50 consensus, ATR14x3 stop.',
        '100% Perp collateral budget, not100% notional: up to3x gross notional subject to costs/free margin.',
        'Realized-only reinvestment; no unrealized sizing, reserve/subsidy or forced100% exposure without signals.',
        'Perp trailing: net-notional arm3%, peak-minus3pp, M15 close observation and next M15 open fill.',
        'Daily loss3%; DD10% observe-only, no DD terminal halt; no fixed daily profit target.',
        'Spot risk H4; Perp risk M15 plus funding/cost; parent H4/funding/cost unchanged.',
        'Historical Spot10/5bps and Perp5/5bps fee/slippage per fill, complete actual funding.', '',
        '| Case | Final USDT | Net profit | Return | Common-grid DD | Closed trades |',
        '|---|---:|---:|---:|---:|---:|']
    for row in receipt['results']:
        s = row['summary']
        net = f"{s['net_return_pct']:.4f}%" if s['net_return_pct'] is not None else 'UNKNOWN'
        lines.append(f"| {row['variant']} | {s['final_equity_known']:.4f} | {s['pnl_after_known_costs']:.4f} | {net} | "
            f"{row['common_grid_audit']['max_drawdown_pct']:.4f}% | {s['closed_trades']} |")
    lines += ['', 'Common-grid DD is passive native M15 Perp marks, Spot H4 as-of; not exact intrabar DD.',
        'Different signal clocks, direction, leverage and costs: not a one-variable market attribution test.',
        'SOL is a new separately frozen supplement. Original BTC/ETH/Spot/source evidence is unchanged.',
        'All summaries/journals/sidecars/audits reproduced offline; no model, gate or execution activation.', '']
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Offline full-capital Spot vs Perp BTC/ETH/SOL research')
    for flag in ('frozen-root','sol-inputs','report-root'):
        parser.add_argument('--'+flag, required=True)
    args = parser.parse_args(argv)
    run_study(args.frozen_root, args.sol_inputs, args.report_root)


if __name__ == '__main__':
    main()
