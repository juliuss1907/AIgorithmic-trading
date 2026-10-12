"""Mechanical verdict for the pre-registered rotating-position study; criteria were fixed before the run.

A pass means only that a design earns its own frozen prospective test. The window is already seen, so
no result here activates anything or changes the ADR-005 candidate.
"""

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import sys

from intraday.replay_v2.donchian_h1_verdict import DRAWDOWN_LIMIT_PCT, equity_at, hold_profile, split_for

THREE, FOUR = 'ETH-NEAR-SOL', 'ETH-NEAR-SOL-ZEC'
BLOCKS = ('pnl_gate', 'position_limit', 'rotation_margin', 'one_position_per_coin')


def mechanism_profile(report_directory):
    """How often each mechanism acted (descriptive)."""
    counts = Counter()
    with open(Path(report_directory)/'events.jsonl') as stream:
        for line in stream:
            event = json.loads(line)
            if event['kind'] == 'rotation':
                counts['rotations'] += 1
            elif event['kind'] == 'entry_blocked' and event['reason'] in BLOCKS:
                counts[event['reason']] += 1
    return dict(counts)


def judge(receipt, normal, stressed, control):
    """The five criteria of the H1 study, with `control` as the return comparator."""
    markets = {m: sum(v['net_pnl'] for k, v in normal['summary']['contributions'].items() if k.startswith(m+':'))
               for m in ('spot', 'perp')}
    capital, final = Decimal(str(normal['summary']['initial_capital'])), Decimal(str(normal['summary']['final_equity_known']))
    cut = split_for(receipt)
    split = equity_at(normal['report_directory'], cut)
    halves = dict(before=float(split-capital), after=float(final-split))
    s = normal['summary']
    criteria = [
        dict(name='net_return_not_below_control', passed=s['net_return_pct'] >= control['summary']['net_return_pct'],
             candidate=s['net_return_pct'], control=control['summary']['net_return_pct'], control_case=control['variant']),
        dict(name='max_drawdown_below_15pct', passed=s['max_drawdown_known_pct'] < DRAWDOWN_LIMIT_PCT,
             candidate=s['max_drawdown_known_pct'], limit=DRAWDOWN_LIMIT_PCT),
        dict(name='doubled_costs_still_positive', passed=stressed['summary']['net_return_pct'] > 0,
             cost2x=stressed['summary']['net_return_pct']),
        dict(name='spot_and_perp_net_positive', passed=all(v > 0 for v in markets.values()), **markets),
        dict(name='both_halves_positive', passed=all(v > 0 for v in halves.values()), split=cut.isoformat(), **halves),
    ]
    return dict(advance=all(c['passed'] for c in criteria), criteria=criteria)


def describe(item):
    s = item['summary']
    drawdown = s['max_drawdown_known_pct']
    return dict(net_return_pct=s['net_return_pct'], max_drawdown_pct=drawdown,
                return_per_drawdown=(s['net_return_pct']/drawdown if drawdown else None),
                trades=s['closed_trades'], daily_stops=s['daily_stops'], exit_reasons=s['exit_reasons'],
                mechanisms=mechanism_profile(item['report_directory']),
                hold_profile=hold_profile(item['report_directory']))


def evaluate_rotation(receipt):
    by_name = {item['variant']: item for item in receipt['results']}
    result = dict(reference={}, candidates={}, ablations={})
    for label in (f'{THREE}-H4', f'{THREE}-FIXED5050'):
        result['reference'][label] = describe(by_name[label])
        stress = by_name.get(label+'-cost2x')
        if stress:
            result['reference'][label]['cost2x_net_return_pct'] = stress['summary']['net_return_pct']
    for prefix, control in ((THREE, f'{THREE}-FIXED5050'), (FOUR, f'{FOUR}-ROT-NOLIMIT')):
        for design in ('ROT', 'ROT-SLOT'):
            name = f'{prefix}-{design}'
            if name not in by_name or name+'-cost2x' not in by_name or control not in by_name:
                continue
            entry = describe(by_name[name])
            entry.update(judge(receipt, by_name[name], by_name[name+'-cost2x'], by_name[control]))
            result['candidates'][name] = entry
        for label in ('NOGATE', 'NOSTOP', 'NOROT', 'NOLIMIT', 'BARE'):
            name = f'{prefix}-ROT-{label}'
            if name in by_name:
                result['ablations'][name] = describe(by_name[name])
    result['advancing'] = [n for n, v in result['candidates'].items() if v['advance']]
    result['note'] = ('Seen data. Three coins cannot bind a three-position limit, so rotation is only exercised in '
                      'the four-coin family, where ZEC stands in for HYPE. A pass proposes a frozen prospective '
                      'test, never activation.')
    return result


def main(argv=None):
    path = Path((argv or sys.argv[1:])[0])
    print(json.dumps(evaluate_rotation(json.loads(path.read_text())), indent=2, default=str))


if __name__ == '__main__':
    main()
