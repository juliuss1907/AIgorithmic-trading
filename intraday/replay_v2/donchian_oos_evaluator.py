"""Pure locked scoring plus checksum-verified immutable report loading."""

from datetime import datetime
from decimal import Decimal
import json
import math
from pathlib import Path
import re

from intraday.replay_v2.artifacts import read_report, _write, _manifest, _verified_file, SERIES
from intraday.replay_v2.donchian_oos_config import cases, OOSConfig
from intraday.replay_v2.donchian_filter_data import verify_files
from intraday.replay_v2.metrics import encoded, fingerprint


CRITERIA_PATH = Path(__file__).resolve().parents[2]/'docs'/'donchian-oos-criteria.json'


def load_criteria(path=CRITERIA_PATH):
    criteria = json.loads(Path(path).read_text())
    if (criteria['version'] != 'donchian-holdout-1' or
            criteria['cases'] != [name for name,_ in cases()] or
            criteria['research_only'] is not True or criteria['activation_allowed'] is not False or
            criteria['official_gate_eligible'] is not False or
            criteria['primary'] != 'A4-Donchian30-10' or criteria['stress'] != 'A4-Donchian30-10-cost2'):
        raise ValueError('unsupported research criteria')
    for v in (*criteria['mandatory'].values(), *criteria['hypotheses'].values()):
        number(v)
    if not 0 <= criteria['hypotheses']['minimum_passes'] <= 6:
        raise ValueError('invalid required hypothesis count')
    return criteria


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('missing/non-numeric metric')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite metric')
    return result


def evaluate(receipt, criteria):
    """No disk reads or writes; callers must verify artifact hashes first."""
    base = dict(criteria_checksum=fingerprint(criteria), research_only=True,
                activation_allowed=False, official_gate_eligible=False)
    try:
        start,end = (datetime.fromisoformat(receipt['window'][k]) for k in ('start','end'))
        expected = dict(cases(start,end))
        results = receipt['results']
        names = [item['variant'] for item in results]
        if len(set(names)) != 7 or set(names) != set(expected) or len(names) != 7:
            raise ValueError('exactly seven distinct confirmation cases required; actual stress missing?')
        datasets = {item['dataset_checksum'] for item in results}
        if len(datasets) != 1 or not re.fullmatch('[a-f0-9]{64}', next(iter(datasets))):
            raise ValueError('cases must share one SHA256 dataset')
        summaries = {}
        for item in results:
            name = item['variant']
            config = OOSConfig.model_validate({k:v for k,v in item['config'].items() if k in OOSConfig.model_fields})
            if config != expected[name] or item['status'] != 'complete' or item['deterministic_rerun_verified'] is not True:
                raise ValueError('wrong config, incomplete or unverified case: '+name)
            s = item['summary']
            if s['funding_complete'] is not True:
                raise ValueError('incomplete funding: '+name)
            for key in ('net_pnl','net_return_pct','initial_capital','final_equity_known',
                        'max_drawdown_known_pct','exchange_fee_known','slippage_cost_known'):
                number(s[key])
            if (number(s['initial_capital']) != 1000 or number(s['max_drawdown_known_pct']) < 0 or
                    number(s['exchange_fee_known']) < 0 or number(s['slippage_cost_known']) < 0 or
                    not math.isclose(s['final_equity_known']-1000,s['net_pnl'],abs_tol=1e-8) or
                    not math.isclose(s['net_return_pct'],s['net_pnl']/10,abs_tol=1e-8)):
                raise ValueError('summary reconciliation failed: '+name)
            summaries[name] = s
        a0,a1,a2,a3,a4 = (summaries[f'A{i}-Donchian30-10'] for i in range(5))
        stress, twenty = summaries[criteria['stress']],summaries['A4-Donchian20-10']
        for market in ('spot','perp'):
            number(a4['contributions'][market+':ETHUSDT']['net_pnl'])
    except (KeyError, ValueError, TypeError, ArithmeticError, StopIteration) as exc:
        return dict(base,verdict='Invalid',errors=[str(exc)],mandatory={},hypotheses={},hypotheses_passed=0)

    def check(value, threshold, *, strict=False, minimum=False):
        if value is None:
            return dict(status='not_evaluable',value=None,threshold=float(threshold))
        value,threshold = number(value),number(threshold)
        passed = value > threshold if strict else value >= threshold if minimum else value <= threshold
        return dict(status='pass' if passed else 'fail',value=float(value),threshold=float(threshold))

    def ratio(numerator, denominator):
        return number(numerator)/number(denominator) if number(denominator)>0 else None

    m,h = criteria['mandatory'],criteria['hypotheses']
    mandatory = dict(G1=check(a4['net_pnl'],m['net_min_exclusive'],strict=True),
                     G2=check(stress['net_pnl'],m['stress_net_min_exclusive'],strict=True),
                     G3=check(a4['max_drawdown_known_pct'],m['max_drawdown_pct']))
    primary_ratio=ratio(a4['net_return_pct'],a4['max_drawdown_known_pct'])
    hypotheses = dict(
        H1=check(ratio(number(a4['exchange_fee_known'])+number(a4['slippage_cost_known']),
                       number(a0['exchange_fee_known'])+number(a0['slippage_cost_known'])),h['cost_ratio_max']),
        H2=check(ratio(a2['max_drawdown_known_pct'],a1['max_drawdown_known_pct']),h['adx_drawdown_ratio_max']),
        H3=check(a4['net_pnl'],twenty['net_pnl'],minimum=True),
        H4=check(min(number(a4['contributions'][market+':ETHUSDT']['net_pnl'])
                     for market in ('spot','perp')),0,strict=True))
    for key,other in (('H5',a1),('H6',a3)):
        other_ratio=ratio(other['net_return_pct'],other['max_drawdown_known_pct'])
        hypotheses[key] = (check(primary_ratio,other_ratio,minimum=True)
                           if other_ratio is not None else dict(status='not_evaluable',value=None,threshold=None))
    count=sum(item['status']=='pass' for item in hypotheses.values())
    verdict = ('Fail' if any(item['status']!='pass' for item in mandatory.values()) else
               'Pass' if count>=h['minimum_passes'] else 'Inconclusive')
    return dict(base,verdict=verdict,mandatory=mandatory,hypotheses=hypotheses,hypotheses_passed=count,
                recommendation='operator_may_consider_prospective_paper' if verdict != 'Fail' else 'do_not_paper_this_configuration')


def verified_receipt(path):
    path=Path(path).expanduser().resolve()
    raw=json.loads(path.read_text())
    if (raw.get('research_only') is not True or raw.get('activation_allowed') is not False or
            raw.get('official_gate_eligible') is not False):
        raise ValueError('comparison must remain research-only and ineligible for activation')
    verify_files(raw.get('source_files_sha256',{}))
    from intraday.replay_v2.portfolio_study import file_hash
    if file_hash(Path(raw['inputs_path'])) != raw['inputs_sha256'] or raw['source_unchanged'] is not True:
        raise ValueError('input evidence changed')
    results=[]
    for item in raw['results']:
        directory=Path(item['report_directory']).resolve()
        if directory.name != item['run_id']:
            raise ValueError('report identity mismatch')
        report=read_report(directory.parent,item['run_id'])
        _,manifest=_manifest(directory.parent,item['run_id'])
        for series in SERIES:
            with _verified_file(directory/(series+'.jsonl'),manifest['files'][series+'.jsonl']):
                pass
        if (report['summary'] != item['summary'] or report['result_id'] != item['result_id'] or
                report['status'] != item['status'] or ('config' in item and report['config']!=item['config']) or
                report['inputs']['dataset_checksum'] != item['dataset_checksum'] or
                report['inputs']['config_checksum'] != fingerprint(report['config']) or
                report['research_only'] is not True or report['activation_allowed'] is not False):
            raise ValueError('comparison does not bind verified report')
        results.append({**item,'config':report['config']})
    return {**raw,'results':results}


def markdown(result):
    lines=['# Donchian historical holdout verdict','',f"Verdict: {result['verdict']}",
           f"Hypotheses passed: {result['hypotheses_passed']}/6",'',
           'Research only; no activation, official gate writes or automatic fallback.','',
           '| Criterion | Status | Value | Threshold |','|---|---|---:|---:|']
    for group in ('mandatory','hypotheses'):
        for key,item in result[group].items():
            lines.append(f"| {key} | {item['status']} | {item['value']} | {item['threshold']} |")
    lines.extend(['',*result.get('errors',[]),''])
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='Score verified historical holdout research; never activate')
    parser.add_argument('--comparison',required=True)
    parser.add_argument('--criteria',default=str(CRITERIA_PATH))
    parser.add_argument('--output-root',required=True)
    args=parser.parse_args(argv)
    criteria=load_criteria(args.criteria)
    try:
        result=evaluate(verified_receipt(args.comparison),criteria)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        result=dict(verdict='Invalid',errors=[str(exc)],mandatory={},hypotheses={},hypotheses_passed=0,
                    research_only=True,activation_allowed=False,official_gate_eligible=False)
    root=Path(args.output_root).expanduser().resolve()
    root.mkdir(parents=True,mode=0o700,exist_ok=False)
    _write(root/'evaluation.json',encoded(result))
    _write(root/'evaluation.md',markdown(result))
    print(markdown(result))


if __name__=='__main__':
    main()
