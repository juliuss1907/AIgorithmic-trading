"""Factorial controls and human-readable evidence for realized/trailing research."""

from datetime import datetime, timedelta, timezone


PRESET = 'perp-realized-trailing'


def study_configs(start, end, trend_filter):
    from intraday.replay_v2.historical_mixed import HistoricalConfig
    mixed = [('Spot+Perp', HistoricalConfig(start=start, end=end, trend_filter=trend_filter,
        capital_growth=growth, perp_stop='atr14-3x', perp_trade_exit=exit_mode,
        perp_daily_policy='target5' if exit_mode == 'baseline' else 'none'))
        for growth in ('capped', 'realized') for exit_mode in ('baseline', 'net-trailing-3pp')]
    controls = [('Spot-only control', HistoricalConfig(start=start, end=end, trend_filter=trend_filter,
        include_perp=False, capital_growth=growth)) for growth in ('capped', 'realized')]
    return mixed+controls


def comparison_markdown(receipt):
    local = timezone(timedelta(hours=7))
    lines = ['# Separate realized sizing + per-trade trailing — 24-month research', '',
        'Research only; not a Jev/LLM replay, independent holdout or official activation gate.', '',
        'Window UTC+7: '+' → '.join(datetime.fromisoformat(receipt['window'][k]).astimezone(local).isoformat()
                                  for k in ('start', 'end')),
        'Capital 1,000 USDT: initial Spot 600, Perp NOTIONAL 300, unallocated 100; isolated 3x.',
        'Spot BTC/ETH/SOL/NEAR/ZEC 40/20/20/10/10; Perp BTC/ETH 50/50.',
        'Capped sizing is the historical shared min(initial capital, marked equity) control.',
        'Realized sizing uses separate realized Spot/Perp capital; settled costs/funding charged once; no cross-market profit transfers.',
        'No unrealized PnL grows realized budgets; marked equity remains authoritative for all risk guards.',
        'All Perp entries retain frozen ATR14 x3 and Donchian exits. Parent/Perp daily loss -3% retained.',
        'Trailing replaces the fixed daily profit cap: arm at +3% net trade return, floor observed peak minus 3 percentage points.',
        'Percentages use frozen entry notional, not margin. Floors never lower or reset at UTC midnight.',
        'Policy: '+receipt.get('drawdown_policy', 'terminal')+'. Research checks still require peak DD below 10%.', '',
        '| Portfolio | Sizing | Exit policy | Final USDT | Net return | Peak DD | Spot PnL | Perp PnL | Perp trades | Trailing exits | Check |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|']
    def number(value):
        return f'{value:.4f}' if value is not None else 'UNKNOWN'
    for row in receipt['results']:
        s = row['summary']
        totals = {m: sum(c['net_pnl'] for k, c in s['contributions'].items() if k.startswith(m+':'))
                  for m in ('spot', 'perp')}
        count = sum(c['closed_trades'] for k, c in s['contributions'].items() if k.startswith('perp:'))
        exit_policy = (row['perp_trade_exit'] if row['perp_trade_exit'] != 'baseline' else 'daily target5') if row['include_perp'] else '—'
        lines.append(f"| {row['variant']} | {row['capital_growth']} | {exit_policy} | {number(s['final_equity_known'])} | "
            f"{number(s['net_return_pct'])}% | {number(s['max_drawdown_known_pct'])}% | "
            f"{number(totals['spot'])} | {number(totals['perp'])} | {count} | "
            f"{s.get('perp_trade_trailing', {}).get('exit_count', 0)} | {s['economic_check_only']} |")
    lines += ['', '## Paired comparisons', '',
        '| Portfolio | Sizing | Exit mode | Return minus same-exit capped, pp | Return minus same-sizing Spot, pp | Return minus same-sizing target5, pp |',
        '|---|---|---|---:|---:|---:|']
    for row in receipt['results']:
        lines.append(f"| {row['variant']} | {row['capital_growth']} | {row['perp_trade_exit']} | "
            f"{number(row['paired_capped_return_difference_pp'])} | "
            f"{number(row['paired_spot_control_return_difference_pp'])} | "
            f"{number(row['paired_target5_return_difference_pp'])} |")
    lines += ['', '## Evidence and limitations', '',
        'Native contract 4h closes update trailing peaks. Close breach latches until next contract open; open gaps exit at open.',
        'No favorable high/low ordering is invented. ATR touch stops retain the existing close-detection model.',
        'Projected net return includes entry costs, settled funding and estimated exit costs. Real fills/gaps can miss the floor.',
        'Existing positions are not resized; committed entry principal and marked exposure constrain new budgets.',
        'Per-trade holding hours, entry notional, trailing peak/floor/trigger are in trades.jsonl; update/trigger events in events.jsonl.',
        'Per-market realized capital, sleeve daily loss, costs, funding and six-month periods are in summary/curve journals.',
        'All six summaries and complete journals reproduced offline. Source and frozen snapshots unchanged; no network or LLM.',
        'A positive total portfolio does not prove Perp profitability; no automatic winner or activation.', '']
    return '\n'.join(lines)
