"""Three explicit frozen-only comparisons; full-invested buy/hold benchmark."""

from datetime import datetime
from pathlib import Path

from intraday.replay_v2.artifacts import _write, read_report
from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.historical_study import read_inputs, decode_inputs
from intraday.replay_v2.hold_margin_research import AllocationConfig, simulate_allocation, simulate_buy_hold
from intraday.replay_v2.intraday_data import decode_inputs as decode_native
from intraday.replay_v2.intraday_study import verify_reference
from intraday.replay_v2.metrics import encoded
from intraday.replay_v2.portfolio_study import file_hash
from intraday.replay_v2.short_reserve_book import ShortReserveConfig
from intraday.replay_v2.short_reserve_engine import simulate_short_reserve
from intraday.replay_v2.short_reserve_study import publish_verified


NAMES = ('spot-inputs.json', 'perp-inputs.json', 'intraday-inputs.json')


def run_study(frozen_root, report_root, *, progress=print):
    source = Path(frozen_root).expanduser().resolve()
    prior = read_inputs(source/'comparison.json')
    if prior.get('preset') != 'perp-short-reserve' or set(prior['snapshot_files_sha256']) != set(NAMES):
        raise ValueError('hold-margin requires the frozen short-reserve study with canonical exports')
    hashes = {name:file_hash(source/name) for name in NAMES}
    if hashes != prior['snapshot_files_sha256']:
        raise ValueError('frozen inputs checksum changed')
    database = Path(prior['original_source'])
    if file_hash(database) != prior['source_sha256']:
        raise ValueError('immutable original source changed')
    root = Path(report_root).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in NAMES:
        _write(root/name, encoded(read_inputs(source/name)))
        if file_hash(root/name) != hashes[name]:
            raise ValueError('copied input export does not preserve checksum')
    start, end = (datetime.fromisoformat(prior['window'][key]) for key in ('start','end'))
    base = ShortReserveConfig(start=start, end=end)
    raw = read_inputs(root/'spot-inputs.json')
    spot = {s:tuple(Candle.from_row(row) for row in rows) for s,rows in raw['candles'].items()}
    perp, funding = decode_inputs(base, read_inputs(root/'perp-inputs.json'))
    native = decode_native(base, read_inputs(root/'intraday-inputs.json'), perp)
    controls = []
    for item in prior['results'][2:]:
        loaded = read_report(source/'reports', item['run_id'])
        cfg = ShortReserveConfig.model_validate({k:v for k,v in loaded['config'].items() if k in ShortReserveConfig.model_fields})
        progress('verify prior '+item['variant'])
        control = simulate_short_reserve(cfg, spot, raw['daily'], perp, funding, native)
        verify_reference(source/'reports'/item['run_id'], control)
        controls.append(dict(variant=item['variant'], result_id=control['result_id'], full_journals_verified=True))
    configs = [
        ('Spot-Donchian-Short1x-restore-repay', AllocationConfig(start=start, end=end,
            leverage=1, reserve_policy='restore-and-repay')),
        ('Spot-Hold60-PerpLongShort2x', AllocationConfig(start=start, end=end,
            spot_strategy='buy-and-hold', perp_direction='long-short')),
        ('Spot-Hold100-only', None)]
    results = []
    for name, config in configs:
        progress('replay '+name)
        if config is None:
            report = simulate_buy_hold(start, end, spot, base.weights)
            repeated = simulate_buy_hold(start, end, spot, base.weights)
        else:
            report = simulate_allocation(config, spot, raw['daily'], perp, funding, native)
            restored = AllocationConfig.model_validate({k:v for k,v in report['config'].items() if k in AllocationConfig.model_fields})
            repeated = simulate_allocation(restored, spot, raw['daily'], perp, funding, native)
        report['inputs'].update(source_sha256=prior['source_sha256'], snapshot_files_sha256=hashes,
            base_dataset_checksum=prior['results'][0]['base_dataset_checksum'])
        item = publish_verified(root, name, report, repeated, spot, native)
        results.append(item)
        progress(encoded(dict(variant=name, summary=item['summary'])))
    if (file_hash(database) != prior['source_sha256'] or
            any(file_hash(source/name) != value or file_hash(root/name) != value for name,value in hashes.items())):
        raise ValueError('immutable hold-margin evidence changed')
    receipt = dict(preset='hold-margin-comparison', window=prior['window'], research_only=True,
        activation_allowed=False, official_gate_eligible=False, original_source=str(database),
        source_sha256=prior['source_sha256'], source_unchanged=True, prior_study=str(source),
        prior_study_sha256=file_hash(source/'comparison.json'), snapshot_files_sha256=hashes,
        controls=controls, results=results)
    _write(root/'comparison.json', encoded(receipt))
    _write(root/'comparison.md', comparison_markdown(receipt))
    return receipt


def comparison_markdown(receipt):
    lines = ['# Hold and Perp margin comparison', '',
        'Research only; same frozen703-day window, initial1,000USDT, known fill costs and actual funding.',
        'Spot/Perp/reserve60/30/10 for mixed cases; hold-only invests100% including entry costs.',
        'Spot weights BTC/ETH/SOL/NEAR/ZEC40/20/20/10/10; Perp BTC/ETH50/50 margin budget300.',
        'Buy/hold means fixed quantities from first open until final close, no stop, risk sale or rebalance.',
        'Mixed daily parent3% blocks/flattens Perp only when Spot is held; Perp daily3%, M15 risk, emergency price10%, H4/D1 Donchian30/8, no trailing.',
        'DD10% observe-only, no DD halt. Hold-only is a benchmark, not a six-trade strategy gate.', '',
        '| Case | Final USDT | Return | Spot net | Perp net | Common-M15 portfolio DD | Draw / repay | Reserve |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in receipt['results']:
        s = row['summary']
        spot = sum(v['net_pnl'] for k,v in s['contributions'].items() if k.startswith('spot:'))
        perp = sum(v['net_pnl'] for k,v in s['contributions'].items() if k.startswith('perp:'))
        reserve = s.get('reserve',{})
        net = f"{s['net_return_pct']:.4f}%" if s['net_return_pct'] is not None else 'UNKNOWN'
        lines.append(f"| {row['variant']} | {s['final_equity_known']:.4f} | {net} | {spot:.4f} | {perp:.4f} | "
            f"{row['common_grid_audit']['max_drawdown_pct']:.4f}% | {reserve.get('drawn',0):.4f} / "
            f"{reserve.get('repaid',0):.4f} | {reserve.get('final',0):.4f} |")
    lines += ['', 'Different allocation utilization and different Spot/Perp policies: this is not a one-variable causal experiment.',
        'Passive common-M15 audit uses native Perp marks, H4 Spot as-of; no execution feedback or exact intrabar DD.',
        'Original short2x summaries and all3 journals unchanged; new summaries/journals/sidecars reproduced offline.',
        'No API, LLM, runtime, gate, worker, source-data, account, push or deployment action.', '']
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Frozen-only hold/Perp research; no activation')
    parser.add_argument('--frozen-root', required=True)
    parser.add_argument('--report-root', required=True)
    args = parser.parse_args(argv)
    run_study(args.frozen_root, args.report_root)


if __name__ == '__main__':
    main()
