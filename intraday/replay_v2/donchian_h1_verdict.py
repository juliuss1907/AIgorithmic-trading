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


def split_for(receipt):
    """The registered split for the full window; for a shorter window, its midpoint floored to a UTC date."""
    window = receipt.get('window')
    if not window:
        return SPLIT
    start, end = (datetime.fromisoformat(window[k]) for k in ('start', 'end'))
    if start < SPLIT < end:
        return SPLIT
    middle = start+(end-start)/2
    return middle.replace(hour=0, minute=0, second=0, microsecond=0)


def equity_at(report_directory, at):
    last = None
    with open(Path(report_directory)/'equity_curve.jsonl') as stream:
        for line in stream:
            row = json.loads(line)
            if datetime.fromisoformat(row['at']) > at:
                break
            last = Decimal(row['equity_known'])
    return last


FAMILY = ('H1-GEOM', 'H1-REGIME', 'H1-EMA50-20', 'H1-ADX30')
LONG_HOLD_HOURS = 48


def hold_profile(report_directory):
    """Where the profit sits: Setup-2 earns on the long tail, so count it."""
    holds, long_gross = [], Decimal(0)
    with open(Path(report_directory)/'trades.jsonl') as stream:
        for line in stream:
            trade = json.loads(line)
            hours = (datetime.fromisoformat(trade['closed_at'])-datetime.fromisoformat(trade['opened_at'])).total_seconds()/3600
            holds.append(hours)
            if hours > LONG_HOLD_HOURS:
                long_gross += Decimal(trade['gross_pnl'])
    holds.sort()
    return dict(trades=len(holds), median_hold_hours=holds[len(holds)//2] if holds else None,
                holds_over_48h=sum(h > LONG_HOLD_HOURS for h in holds), gross_pnl_over_48h=float(long_gross))


def evaluate(receipt, candidate='H1', prefix=None):
    name = prefix or basket_name(H1_BASKET)
    by_name = {item['variant']: item for item in receipt['results']}
    control, normal, stressed = (by_name[f'{name}-{suffix}'] for suffix in ('H4', candidate, candidate+'-cost2x'))
    h4, h1, h1_cost = control['summary'], normal['summary'], stressed['summary']
    markets = {m: sum(v['net_pnl'] for k, v in h1['contributions'].items() if k.startswith(m+':'))
               for m in ('spot', 'perp')}
    capital, final = Decimal(str(h1['initial_capital'])), Decimal(str(h1['final_equity_known']))
    cut = split_for(receipt)
    split = equity_at(normal['report_directory'], cut)
    halves = dict(before=float(split-capital), after=float(final-split))
    criteria = [
        dict(name='net_return_not_below_h4_control', passed=h1['net_return_pct'] >= h4['net_return_pct'],
             h1=h1['net_return_pct'], h4=h4['net_return_pct']),
        dict(name='max_drawdown_below_15pct', passed=h1['max_drawdown_known_pct'] < DRAWDOWN_LIMIT_PCT,
             h1=h1['max_drawdown_known_pct'], limit=DRAWDOWN_LIMIT_PCT),
        dict(name='doubled_costs_still_positive', passed=h1_cost['net_return_pct'] > 0,
             h1_cost2x=h1_cost['net_return_pct']),
        dict(name='spot_and_perp_net_positive', passed=all(v > 0 for v in markets.values()), **markets),
        dict(name='both_halves_positive', passed=all(v > 0 for v in halves.values()), split=cut.isoformat(), **halves),
    ]
    return dict(advance=all(c['passed'] for c in criteria), criteria=criteria, candidate=candidate,
                trades=dict(h4=h4['closed_trades'], h1=h1['closed_trades']),
                costs=dict(h4=h4['exchange_fee_known']+h4['slippage_cost_known'],
                           h1=h1['exchange_fee_known']+h1['slippage_cost_known']),
                note='Seen data; a pass proposes a new frozen prospective test, never activation.')


WIDE_FAMILY = ('H1', 'H1-GEOM', 'H1-ADX30', 'H1-EMA50-20', 'H1-REGIME', 'H1-SLOWREGIME')
SLOWREGIME_SET = ('H1', 'H1-REGIME', 'H1-SLOWREGIME')


def slot_profile(report_directory, window_hours):
    """How the five slots were used and how concentrated the result is (descriptive only)."""
    directory = Path(report_directory)
    open_hours, per_coin, blocked = Decimal(0), {}, 0
    with open(directory/'trades.jsonl') as stream:
        for line in stream:
            trade = json.loads(line)
            hours = (datetime.fromisoformat(trade['closed_at'])-datetime.fromisoformat(trade['opened_at'])).total_seconds()/3600
            open_hours += Decimal(str(hours))
            per_coin[trade['symbol']] = per_coin.get(trade['symbol'], Decimal(0))+Decimal(trade['net_pnl'])
    with open(directory/'events.jsonl') as stream:
        for line in stream:
            if '"slot_limit"' in line:
                blocked += 1
    total = sum(per_coin.values(), Decimal(0))
    top5 = sum(sorted(per_coin.values(), reverse=True)[:5], Decimal(0))
    return dict(average_open_positions=float(open_hours/Decimal(str(window_hours))), signals_blocked_by_slots=blocked,
                coins_traded=len(per_coin), net_pnl=float(total),
                top5_coins_net_pnl=float(top5), top5_share_of_net=float(top5/total) if total > 0 else None)


def evaluate_wide(receipt):
    """The H1 family on the wide basket against its own H4 control, plus the random-selection control."""
    by_name = {item['variant']: item for item in receipt['results']}
    window = receipt['window']
    hours = (datetime.fromisoformat(window['end'])-datetime.fromisoformat(window['start'])).total_seconds()/3600
    result = {}
    for candidate in WIDE_FAMILY:
        if f'WIDE-{candidate}-cost2x' not in by_name or f'WIDE-{candidate}' not in by_name:
            continue  # A subset run (`--only`) evaluates what it contains.
        entry = evaluate(receipt, candidate, prefix='WIDE')
        entry['hold_profile'] = hold_profile(by_name[f'WIDE-{candidate}']['report_directory'])
        entry['slot_profile'] = slot_profile(by_name[f'WIDE-{candidate}']['report_directory'], hours)
        result[candidate] = entry
    control = by_name['WIDE-H4']
    result['H4'] = dict(hold_profile=hold_profile(control['report_directory']),
                        slot_profile=slot_profile(control['report_directory'], hours),
                        net_return_pct=control['summary']['net_return_pct'],
                        max_drawdown_pct=control['summary']['max_drawdown_known_pct'])
    random_control = by_name.get('WIDE-H4-random')
    if random_control:
        result['H4-random'] = dict(net_return_pct=random_control['summary']['net_return_pct'],
            max_drawdown_pct=random_control['summary']['max_drawdown_known_pct'],
            trades=random_control['summary']['closed_trades'],
            ranked_minus_random_pp=control['summary']['net_return_pct']-random_control['summary']['net_return_pct'],
            cost2x_net_return_pct=by_name['WIDE-H4-random-cost2x']['summary']['net_return_pct'])
    return dict(candidates=result, advancing=[c for c in WIDE_FAMILY if c in result and result[c]['advance']],
                note='Wide basket, five slots, deterministic ranking standing in for Jev; seen-data research, never activation.')


def evaluate_slowregime(receipt):
    """Baseline, hybrid H4 regime and the pure-H1 clock-length regime against the same H4 control."""
    name = basket_name(H1_BASKET)
    by_name = {item['variant']: item for item in receipt['results']}
    result = {}
    for candidate in SLOWREGIME_SET:
        entry = evaluate(receipt, candidate)
        entry['hold_profile'] = hold_profile(by_name[f'{name}-{candidate}']['report_directory'])
        result[candidate] = entry
    result['H4'] = dict(hold_profile=hold_profile(by_name[f'{name}-H4']['report_directory']))
    return dict(candidates=result, advancing=[c for c in SLOWREGIME_SET if result[c]['advance']],
                note='Seen data; the slow regime is the pure-H1 counterpart of the H4-regime hybrid.')


def evaluate_family(receipt):
    """Every pre-registered candidate against the same H4 control, plus the H1 baseline for reference."""
    name = basket_name(H1_BASKET)
    by_name = {item['variant']: item for item in receipt['results']}
    result = {}
    for candidate in ('H1',)+FAMILY:
        entry = evaluate(receipt, candidate)
        entry['hold_profile'] = hold_profile(by_name[f'{name}-{candidate}']['report_directory'])
        result[candidate] = entry
    result['H4'] = dict(hold_profile=hold_profile(by_name[f'{name}-H4']['report_directory']))
    return dict(candidates=result, advancing=[c for c in FAMILY if result[c]['advance']],
                note='Four tries on seen data after the H1 baseline failed; a pass proposes a new frozen prospective '
                     'test, never activation.')


def main(argv=None):
    path = Path((argv or sys.argv[1:])[0])
    receipt = json.loads(path.read_text())
    preset = receipt.get('preset')
    verdict = (evaluate_wide(receipt) if preset == 'donchian-wide-slots'
               else evaluate_slowregime(receipt) if preset == 'donchian-setup2-h1-slowregime'
               else evaluate_family(receipt) if preset == 'donchian-setup2-h1-family' else evaluate(receipt))
    output = path.with_name('verdict.json')
    text = json.dumps(verdict, indent=2, sort_keys=True)+'\n'
    if output.exists() and output.read_text() != text:
        raise ValueError('existing verdict changed')
    output.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
