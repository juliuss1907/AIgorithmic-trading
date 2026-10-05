"""Descriptive ADX attribution on already-seen data, never an activation gate."""

from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_adx_config import ADXStudyConfig, cases
from intraday.replay_v2.donchian_filter_data import verify_files
from intraday.replay_v2.donchian_filters import entry_filters
from intraday.replay_v2.donchian_oos_analysis import journal, trade_stats
from intraday.replay_v2.donchian_oos_pipeline import verify_item
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


def rejected_trades(trades, events, threshold):
    entries = {(e['at'], e['market'], e['symbol']): e['features']
               for e in events if e['kind'] == 'entry_features'}
    blocked = []
    for trade in trades:
        features = entries[(trade['opened_at'], trade['market'], trade['symbol'])]
        features = {k: (Decimal(str(v)) if v is not None and k != 'profile' else v)
                    for k, v in features.items()}
        side = 1 if trade['side'] == 'long' else -1
        if entry_filters(features, side, 4, adx_threshold=threshold):
            blocked.append(trade)
    values = [Decimal(str(t['net_pnl'])) for t in blocked]
    return {**trade_stats(blocked),
            'winning_net_sum': float(sum((v for v in values if v > 0), Decimal(0))),
            'losing_net_abs': float(-sum((v for v in values if v < 0), Decimal(0)))}


def describe(comparison_path, output_root):
    source = Path(comparison_path).resolve()
    receipt = read_inputs(source)
    if (receipt['preset'] != 'donchian-adx-exploration' or not receipt['research_on_seen_data'] or
            receipt['activation_allowed'] or receipt['official_gate_eligible'] or not receipt['source_unchanged']):
        raise ValueError('not an immutable exploratory ADX receipt')
    verify_files(receipt['source_files_sha256'])
    if file_hash(receipt['inputs_path']) != receipt['inputs_sha256']:
        raise ValueError('input checksum changed')
    binding = read_inputs(source.parent/'binding.json')
    if fingerprint(binding) != receipt['binding_checksum']:
        raise ValueError('binding checksum changed')
    restored = [ADXStudyConfig.model_validate({k: v for k, v in r['config'].items()
                if k in ADXStudyConfig.model_fields}) for r in receipt['results']]
    expected = cases(restored[0].start, restored[0].end)
    if [(r['variant'], c.model_dump(mode='json')) for r,c in zip(receipt['results'], restored)] != [
            (name, cfg.model_dump(mode='json')) for name,cfg in expected]:
        raise ValueError('five-case configuration changed')
    dataset = {r['dataset_checksum'] for r in receipt['results']}
    if len(dataset) != 1:
        raise ValueError('cases use different datasets')
    off = receipt['results'][0]
    off_trades, off_events = journal(off, 'trades'), journal(off, 'events')
    results = []
    for item, cfg in zip(receipt['results'], restored):
        verify_item(item)
        trades = journal(item, 'trades')
        grouped = defaultdict(list)
        for t in trades:
            grouped[t['market']+'/'+t['symbol']].append(t)
        results.append(dict(variant=item['variant'], summary=item['summary'],
                            trades=trade_stats(trades),
                            coin_market={k: trade_stats(v) for k,v in sorted(grouped.items())},
                            rejected_off_trades=rejected_trades(off_trades, off_events, cfg.adx_threshold)))
    result = dict(research_only=True, research_on_seen_data=True, activation_allowed=False,
                  comparison_sha256=file_hash(source), dataset_checksum=next(iter(dataset)),
                  results=results,
                  attribution_caveat='Conditional on ADX-off executed entries and sizing. Not exact counterfactual PnL: filters change entry timing, capital and subsequent positions.')
    lines = ['# A4 Donchian30/10 — ADX sensitivity 2022–2026', '',
             'Exploratory, already-seen data; not OOS confirmation or approval to trade.', '',
             '| ADX | Final USDT | Net USDT | Return % | DD % | Trades |',
             '|---|---:|---:|---:|---:|---:|']
    for r,c in zip(results, restored):
        s=r['summary']
        lines.append(f"| {c.adx_threshold if c.adx_threshold is not None else 'Off (DMI off)'} | {s['final_equity_known']:.2f} | {s['net_pnl']:.2f} | {s['net_return_pct']:.2f} | {s['max_drawdown_known_pct']:.2f} | {s['closed_trades']} |")
    lines += ['', '## ADX-off entries rejected by each filter', '',
              result['attribution_caveat'], '',
              '| ADX | Rejected trades | Winners | Losers | Winners net USDT | Losses avoided USDT | Rejected net USDT |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for r,c in zip(results, restored):
        s=r['rejected_off_trades']
        lines.append(f"| {c.adx_threshold} | {s['closed_trades']} | {s['wins']} | {s['losses']} | {s['winning_net_sum']:.2f} | {s['losing_net_abs']:.2f} | {s['net_pnl']:.2f} |")
    for r in results:
        s=r['summary']
        lines += ['', '## '+r['variant'], '', '### Coin / market', '',
                  '| Sleeve | Trades | Net USDT | Win % |', '|---|---:|---:|---:|']
        for k,v in r['coin_market'].items():
            win = f"{v['win_rate_pct']:.2f}" if v['win_rate_pct'] is not None else 'N/A'
            lines.append(f"| {k} | {v['closed_trades']} | {v['net_pnl']:.2f} | {win} |")
        lines += ['', '### Annual marked equity', '',
                  '| Year | Opening USDT | Ending USDT | Change USDT | Return % |',
                  '|---|---:|---:|---:|---:|']
        for year,v in s['annual_marked_equity'].items():
            lines.append(f"| {year} | {v['start_equity_usdt']:.2f} | {v['end_equity_usdt']:.2f} | {v['marked_change_usdt']:.2f} | {v['return_pct']:.2f} |")
        lines += ['', '### Calendar half-years', '',
                  '| Window UTC | Complete half | Net USDT | Return % | DD % |',
                  '|---|---|---:|---:|---:|']
        for v in s['six_month_periods']:
            lines.append(f"| {v['start'][:10]} → {v['end'][:10]} | {v['full_calendar_half']} | {v['pnl']:.2f} | {v['return_pct']:.2f} | {v['max_drawdown_pct']:.2f} |")
        lines += ['', 'Worst drawdown episode (UTC): '+encoded(s['drawdown_episode']), '',
                  'Full cost/risk summary: '+encoded(s)]
    lines += ['', '## Frozen rules and caveats', '',
              '1000USDT; Spot60/Short40; BTC40/ETH30/SOL30 each. Short-only1x, realized-only sleeve reinvestment; no transfers.',
              'Donchian30/10 H4; EMA200/50; preceding VolumeMA20x1.2; approximate prior20day M15 volume profile retained.',
              'ATR14x3 H4 trailing, native M15 execution/risk; UTC daily loss3%, DD observe-only. No terminal DD halt.',
              'Enabled ADX: Wilder14, strictly above threshold and rising, directional DMI. Off disables both ADX/DMI.',
              'Per-fill Spot fee/slippage10/5bps; Perp5/5bps plus historical funding. No account fee verification.',
              'Continuous account/positions across October2024 join. 2026 and final half-year are partial.',
              'Original source prices unchanged: eight explicit H4/M15 price-view exceptions. No generic tolerance.',
              'Five missing Spot M15 bars, one stale mark interval and funding-reference substitutions retain approved source policies; no synthetic bars.',
              'DD uses observed known prices; OHLC cannot establish exact tick fills, book depth or liquidation. Daily3% is a trigger, not a guaranteed loss ceiling.',
              'No new thresholds chosen after results; no activation, official gate/champion change or VPS deployment.', '']
    root=Path(output_root).resolve();root.mkdir(parents=True,mode=0o700,exist_ok=False)
    files={name:_write(root/name,body) for name,body in (
        ('analysis.json',encoded(result)),('report.md','\n'.join(lines)))}
    _write(root/'manifest.json',encoded(dict(comparison_sha256=result['comparison_sha256'],files=files)))
    return result
