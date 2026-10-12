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


def concurrency(report_directory):
    """Most positions open at once, in total and per market (descriptive)."""
    points = []
    with open(Path(report_directory)/'trades.jsonl') as stream:
        for line in stream:
            trade = json.loads(line)
            points.append((trade['opened_at'], 1, trade['market']))
            points.append((trade['closed_at'], -1, trade['market']))
    points.sort(key=lambda p: (p[0], p[1]))  # Closes first at equal timestamps.
    live, peak = Counter(), Counter()
    for _, step, market in points:
        live[market] += step
        peak[market] = max(peak[market], live[market])
        peak['total'] = max(peak['total'], sum(live.values()))
    return dict(peak)


WINDOWS = ((30, ''), (25, '-W25'), (20, '-W20'))
DESIGNS = ('ROT-SLOT', 'ROT-SLOT6')


def evaluate_window(receipt):
    """Entry windows 25 and 20 against 30, and the per-sleeve limit against the pooled limit.

    Controls: a design at W25/W20 against the same design at 30; `ROT-SLOT6` at 30 against `ROT-SLOT` at 30;
    `ROT-SLOT` at 30 against `FIXED5050` on three coins (the four-coin pooled design was judged earlier)."""
    by_name = {item['variant']: item for item in receipt['results']}
    result = dict(reference={}, candidates={}, fixed_windows={})
    for label in (f'{THREE}-H4', f'{THREE}-FIXED5050'):
        if label in by_name:
            result['reference'][label] = describe(by_name[label])
    for suffix in ('', '-W25', '-W20'):
        name = f'{THREE}-FIXED5050{suffix}'
        if name in by_name:
            result['fixed_windows'][name] = describe(by_name[name])
    for prefix in (THREE, FOUR):
        for design in DESIGNS:
            for window, tail in WINDOWS:
                name = f'{prefix}-{design}{tail}'
                if name not in by_name or name+'-cost2x' not in by_name:
                    continue
                if tail:
                    control = f'{prefix}-{design}'
                elif design == 'ROT-SLOT6':
                    control = f'{prefix}-ROT-SLOT'
                else:
                    control = f'{THREE}-FIXED5050' if prefix == THREE else None
                entry = describe(by_name[name])
                entry['entry_window'] = window
                entry['concurrency'] = concurrency(by_name[name]['report_directory'])
                if control and control in by_name:
                    entry.update(judge(receipt, by_name[name], by_name[name+'-cost2x'], by_name[control]))
                else:
                    entry.update(advance=None, criteria=[], note='reference only; judged in the earlier rotation study')
                result['candidates'][name] = entry
    result['advancing'] = [n for n, v in result['candidates'].items() if v['advance']]
    result['note'] = ('Seen data. Three coins cannot bind a per-sleeve limit of three; ZEC stands in for HYPE. '
                      'A pass proposes a frozen prospective test, never activation.')
    return result


def main(argv=None):
    args = list(argv or sys.argv[1:])
    window = '--window' in args
    path = Path([a for a in args if not a.startswith('--')][0])
    receipt = json.loads(path.read_text())
    print(json.dumps((evaluate_window if window else evaluate_rotation)(receipt), indent=2, default=str))


if __name__ == '__main__':
    main()
