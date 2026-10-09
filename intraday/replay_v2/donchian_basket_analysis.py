"""Ranking of the ten three-coin baskets under Setup-2 rules; seen data, not OOS."""

from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_adx_config import cases
from intraday.replay_v2.donchian_adx_setups import BASKETS, basket_name
from intraday.replay_v2.donchian_filter_data import verify_files
from intraday.replay_v2.donchian_oos_analysis import journal
from intraday.replay_v2.donchian_oos_pipeline import verify_item
from intraday.replay_v2.donchian_setups_analysis import outcomes
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash

# First day of the 703-day seen window; earlier data is the 2022-2024 OOS holdout.
SPLIT = datetime(2024, 10, 29, tzinfo=timezone.utc)


def split_returns(curve, capital, split=SPLIT):
    """Marked return before and after the split, from the first observation at or after it."""
    at_split = next((D(r['equity_known']) for r in curve if datetime.fromisoformat(r['at']) >= split), None)
    if at_split is None or datetime.fromisoformat(curve[0]['at']) >= split:
        return None, None  # Window does not straddle the split.
    final = D(curve[-1]['equity_known'])
    return float((at_split/D(capital)-1)*100), float((final/at_split-1)*100)


def basket_row(name, item, stress):
    verify_item(item)
    s = item['summary']
    trades = journal(item, 'trades')
    stats = outcomes(trades)
    if abs(stats['net_pnl']-s['net_pnl']) > 1e-7:
        raise ValueError('closed-trade outcomes do not reconcile with capital: '+name)
    early, late = split_returns(journal(item, 'equity_curve'), s['initial_capital'])
    halves = [p for p in s['six_month_periods'] if p['full_calendar_half']]
    if stress is not None:
        verify_item(stress)
    return dict(basket=name, return_pct=s['net_return_pct'], max_drawdown_pct=s['max_drawdown_known_pct'],
                return_to_drawdown=(s['net_return_pct']/s['max_drawdown_known_pct']
                                    if s['max_drawdown_known_pct'] else None),
                trades=s['closed_trades'], win_rate_pct=stats['win_rate_pct'], profit_factor=stats['profit_factor'],
                return_cost2x_pct=stress['summary']['net_return_pct'] if stress else None,
                return_2022_2024_pct=early, return_2024_2026_pct=late,
                losing_halves=sum(p['return_pct'] < 0 for p in halves), full_halves=len(halves),
                daily_stops=s['daily_stops'])


def rank(comparison_path, output_root):
    source = Path(comparison_path).resolve()
    receipt = read_inputs(source)
    if (receipt['preset'] != 'donchian-basket3' or not receipt['research_on_seen_data'] or
            receipt['activation_allowed'] or receipt['official_gate_eligible'] or not receipt['source_unchanged']):
        raise ValueError('requires a verified research-only basket receipt')
    binding = read_inputs(source.parent/'binding.json')
    if fingerprint(binding) != receipt['binding_checksum'] or file_hash(receipt['inputs_path']) != receipt['inputs_sha256']:
        raise ValueError('research source binding changed')
    verify_files(receipt['source_files_sha256'])
    start, end = (datetime.fromisoformat(receipt['window'][k]) for k in ('start', 'end'))
    expected = [n for n, _ in cases(start, end, universe='baskets')]
    results = {r['variant']: r for r in receipt['results']}
    if [r['variant'] for r in receipt['results']] != expected:
        raise ValueError('basket cases changed')
    if len({r['dataset_checksum'] for r in receipt['results']}) != 1:
        raise ValueError('baskets must use the same validated source dataset')
    rows = sorted((basket_row(basket_name(b), results[basket_name(b)], results[basket_name(b)+'-cost2x'])
                   for b in BASKETS), key=lambda r: (r['return_to_drawdown'] is not None, r['return_to_drawdown'] or 0),
                  reverse=True)
    control = basket_row('Setup-2 (NEAR30/SOL40/ZEC30)', results['Setup-2'], None)
    ranking = dict(source_comparison_sha256=file_hash(source), split=SPLIT.isoformat(), window=receipt['window'],
                   research_on_seen_data=True, selection_is_not_out_of_sample=True, rows=rows, control=control)
    root = Path(output_root)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    _write(root/'ranking.json', encoded(ranking))
    _write(root/'ranking.md', markdown(ranking))
    return ranking


def markdown(ranking):
    f = lambda v, spec: format(v, spec) if v is not None else 'N/A'
    lines = ['# Ten three-coin baskets — Setup-2 rules, equal thirds', '',
             f"Window {ranking['window']['start'][:10]} → {ranking['window']['end'][:10]}, split at "
             f"{ranking['split'][:10]}. Seen data: picking a basket here is selection, not OOS evidence.", '',
             '| # | Basket | Return % | DD % | Ret/DD | Trades | Win % | PF | Cost×2 % | 2022–24 % | 2024–26 % | Losing halves |',
             '|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for i, r in enumerate(ranking['rows']+[ranking['control']], 1):
        lines.append(f"| {i if r is not ranking['control'] else '—'} | {r['basket']} | {r['return_pct']:.2f} | "
                     f"{r['max_drawdown_pct']:.2f} | {f(r['return_to_drawdown'], '.2f')} | {r['trades']} | "
                     f"{f(r['win_rate_pct'], '.1f')} | {f(r['profit_factor'], '.2f')} | {f(r['return_cost2x_pct'], '.2f')} | "
                     f"{f(r['return_2022_2024_pct'], '.2f')} | {f(r['return_2024_2026_pct'], '.2f')} | "
                     f"{r['losing_halves']}/{r['full_halves']} |")
    return '\n'.join(lines)+'\n'


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Rank the verified three-coin basket study; no trading')
    parser.add_argument('--comparison', required=True)
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args(argv)
    print(markdown(rank(args.comparison, args.output_root)))


if __name__ == '__main__':
    main()
