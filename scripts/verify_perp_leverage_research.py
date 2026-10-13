"""Read-only Decimal accounting audit and full-journal legacy regression for this experiment."""

import argparse
from datetime import datetime
from decimal import Decimal as D
import json
from pathlib import Path

from intraday.replay_v2.artifacts import SERIES, _manifest, _verified_file, _write, read_report
from intraday.replay_v2.donchian_adx_config import FiveCoinADXConfig
from intraday.replay_v2.donchian_adx_setups import BasketConfig
from intraday.replay_v2.donchian_adx_study import enriched, runtime_hashes
from intraday.replay_v2.donchian_filter_data import decode_bundle, verify_files
from intraday.replay_v2.donchian_filter_engine import prepare
from intraday.replay_v2.donchian_five_data import read_manifest
from intraday.replay_v2.donchian_oos_pipeline import git, verify_item
from intraday.replay_v2.donchian_rotation_config import RotationConfig
from intraday.replay_v2.intraday_study import verify_reference
from intraday.replay_v2.metrics import encoded, fingerprint, journal_hash
from intraday.replay_v2.portfolio_study import file_hash

TOLERANCE = D('1e-18')
REFERENCES = ('ETH-NEAR-SOL-H4', 'ETH-NEAR-SOL-ROT-SLOT', 'ETH-NEAR-SOL-ZEC-ROT-SLOT')


def equal(actual, expected, label):
    if abs(actual-expected) > TOLERANCE:
        raise ValueError(label+' does not reconcile')


def audit_case(item):
    directory = Path(item['report_directory'])
    report = read_report(directory.parent, item['run_id'])
    _, manifest = _manifest(directory.parent, item['run_id'])
    journals = {}
    for name in SERIES:
        with _verified_file(directory/(name+'.jsonl'), manifest['files'][name+'.jsonl']) as stream:
            journals[name] = [json.loads(line) for line in stream] if name != 'equity_curve' else None
    total = D(0)
    multiplier = report['config'].get('cost_multiplier', 1)
    for t in journals['trades']:
        q, entry, exit_price = (D(t[k]) for k in ('quantity', 'entry_price', 'exit_price'))
        gross = q*(exit_price-entry)*(1 if t['side'] == 'long' else -1)
        equal(D(t['gross_pnl']), gross, 'price PnL')
        fee_rate = D('.0005') if t['market'] == 'perp' else D('.001')
        slip_rate = D('.0005')
        equal(D(t['exchange_fee']), q*(entry+exit_price)*fee_rate*multiplier, 'notional exchange fees')
        equal(D(t['slippage_cost']), q*(entry+exit_price)*slip_rate*multiplier, 'notional slippage')
        expected = gross-sum((D(t[k]) for k in ('exchange_fee', 'slippage_cost', 'funding_paid')), D(0))
        expected -= D(t.get('liquidation_penalty', 0))
        equal(D(t['net_pnl']), expected, 'net trade PnL')
        if t['exit_reason'] == 'liquidation':
            leverage = D(report['config']['leverage'])
            side = 1 if t['side'] == 'long' else -1
            equal(exit_price, entry*(1-side*(1/leverage-D('.004'))), 'liquidation price')
            equal(D(t['margin']), q*entry/leverage, 'isolated margin')
            equal(gross-q*exit_price*(fee_rate+slip_rate)*multiplier-D(t['liquidation_penalty']),
                  -D(t['margin']), 'full margin forfeiture')
            if report['config']['leverage'] <= 1 or D(t['liquidation_penalty']) <= 0:
                raise ValueError('invalid opt-in liquidation')
        total += D(t['net_pnl'])
    funding = sum((D(e['amount']) for e in journals['events'] if e['kind'] == 'funding'), D(0))
    equal(funding, sum((D(t['funding_paid']) for t in journals['trades']), D(0)), 'funding journal')
    for event in journals['events']:
        if event['kind'] == 'entry' and 'margin' in event:
            equal(D(event['margin']), D(event['notional'])/D(event['leverage']), 'entry notional/margin')
    # Published aggregate floats are checked at a looser precision; trade arithmetic above stays Decimal.
    final = D(str(report['summary']['final_equity_known']))-D(str(report['summary']['initial_capital']))
    if abs(total-final) > D('1e-9'):
        raise ValueError('portfolio PnL does not reconcile')
    return dict(variant=item['variant'], result_id=report['result_id'], trades=len(journals['trades']),
                net_pnl=str(total), journals_verified=True, decimal_accounting_verified=True)


def verify_rotation(reference_path, progress=print):
    reference_path = Path(reference_path).expanduser().resolve()
    old = json.loads(reference_path.read_text())
    source = Path(old['inputs_path'])
    if file_hash(source) != old['inputs_sha256']:
        raise ValueError('legacy input manifest changed')
    raw = read_manifest(source)
    start, end = (datetime.fromisoformat(raw['window'][k]) for k in ('start', 'end'))
    cfg = FiveCoinADXConfig(start=start, end=end)
    lineage = raw['source_files_sha256']
    data, funding = decode_bundle(cfg, raw)
    prepared = prepare(cfg, data, funding, native_boundary_policy=raw.get('native_boundary_policy'))
    del raw, data, funding
    results = []
    selected = {i['variant']: i for i in old['results'] if i['variant'] in REFERENCES}
    if set(selected) != set(REFERENCES):
        raise ValueError('requires all three published rotation controls')
    for name in REFERENCES:
        item = selected[name]
        verify_item(item)
        cls = BasketConfig if name.endswith('H4') else RotationConfig
        cfg = cls.model_validate({k: v for k, v in item['config'].items() if k in cls.model_fields})
        report = enriched(cfg, prepared)
        report['summary'] = json.loads(encoded(report['summary']))
        verify_reference(item['report_directory'], report)
        results.append(dict(variant=name, result_id=report['result_id'], summary=report['summary'],
                            journals={s: journal_hash(report[s]) for s in SERIES}, matched=True))
        progress('Matched summary, result ID and every journal: '+name)
    verify_files(lineage)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rotation-reference')
    parser.add_argument('--comparison', action='append', default=[])
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args()
    if not args.rotation_reference and not args.comparison:
        parser.error('supply a rotation reference or at least one comparison')
    root = Path(args.output_root).expanduser().resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    paths = [*args.comparison, *([args.rotation_reference] if args.rotation_reference else [])]
    sources = runtime_hashes()
    binding = dict(commit=git('rev-parse', 'HEAD'), sources=sources,
                   verifier_sha256=file_hash(Path(__file__)), inputs={str(p): file_hash(p) for p in paths})
    _write(root/'binding.json', encoded(binding))
    results = [audit_case(item) for path in args.comparison for item in json.loads(Path(path).read_text())['results']]
    legacy = verify_rotation(args.rotation_reference) if args.rotation_reference else []
    if runtime_hashes() != sources or any(file_hash(p) != h for p, h in binding['inputs'].items()):
        raise ValueError('verification sources or evidence changed')
    _write(root/'verification.json', encoded(dict(binding_checksum=fingerprint(binding), audited=results,
                                                legacy=legacy, verified=True)))
    print(encoded(dict(audited_cases=len(results), legacy_cases=len(legacy), verified=True)))


if __name__ == '__main__':
    main()
