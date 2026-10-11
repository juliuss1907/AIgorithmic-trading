"""Mechanical verdict for the pre-registered Setup-2 H1 study; criteria were fixed before the run.

Passing means only that an H1 variant earns a new frozen prospective test. The window is already
seen, so no result here activates anything.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

from intraday.replay_v2.donchian_adx_setups import H1_BASKET, basket_name

SPLIT = datetime(2024, 10, 29, tzinfo=timezone.utc)  # Start of the earlier 703-day research window.
DRAWDOWN_LIMIT_PCT = 15.0  # The live portfolio halt.


def equity_at(report_directory, at):
    last = None
    with open(Path(report_directory)/'equity_curve.jsonl') as stream:
        for line in stream:
            row = json.loads(line)
            if datetime.fromisoformat(row['at']) > at:
                break
            last = Decimal(row['equity_known'])
    return last


def evaluate(receipt):
    name = basket_name(H1_BASKET)
    by_name = {item['variant']: item for item in receipt['results']}
    control, normal, stressed = (by_name[f'{name}-{suffix}'] for suffix in ('H4', 'H1', 'H1-cost2x'))
    h4, h1, h1_cost = control['summary'], normal['summary'], stressed['summary']
    markets = {m: sum(v['net_pnl'] for k, v in h1['contributions'].items() if k.startswith(m+':'))
               for m in ('spot', 'perp')}
    capital, final = Decimal(str(h1['initial_capital'])), Decimal(str(h1['final_equity_known']))
    split = equity_at(normal['report_directory'], SPLIT)
    halves = dict(before=float(split-capital), after=float(final-split))
    criteria = [
        dict(name='net_return_not_below_h4_control', passed=h1['net_return_pct'] >= h4['net_return_pct'],
             h1=h1['net_return_pct'], h4=h4['net_return_pct']),
        dict(name='max_drawdown_below_15pct', passed=h1['max_drawdown_known_pct'] < DRAWDOWN_LIMIT_PCT,
             h1=h1['max_drawdown_known_pct'], limit=DRAWDOWN_LIMIT_PCT),
        dict(name='doubled_costs_still_positive', passed=h1_cost['net_return_pct'] > 0,
             h1_cost2x=h1_cost['net_return_pct']),
        dict(name='spot_and_perp_net_positive', passed=all(v > 0 for v in markets.values()), **markets),
        dict(name='both_halves_positive', passed=all(v > 0 for v in halves.values()), split=SPLIT.isoformat(), **halves),
    ]
    return dict(advance=all(c['passed'] for c in criteria), criteria=criteria,
                trades=dict(h4=h4['closed_trades'], h1=h1['closed_trades']),
                costs=dict(h4=h4['exchange_fee_known']+h4['slippage_cost_known'],
                           h1=h1['exchange_fee_known']+h1['slippage_cost_known']),
                note='Seen data; a pass proposes a new frozen prospective test, never activation.')


def main(argv=None):
    path = Path((argv or sys.argv[1:])[0])
    verdict = evaluate(json.loads(path.read_text()))
    output = path.with_name('verdict.json')
    text = json.dumps(verdict, indent=2, sort_keys=True)+'\n'
    if output.exists() and output.read_text() != text:
        raise ValueError('existing verdict changed')
    output.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
