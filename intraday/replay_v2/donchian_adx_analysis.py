"""Descriptive ADX attribution on already-seen data, never an activation gate."""

from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_adx_config import config_type, cases
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


def describe(comparison_path, output_root, *, baseline_path=None):
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
    universe=receipt['results'][0]['config'].get('universe','three')
    model=config_type(universe)
    restored = [model.model_validate({k: v for k, v in r['config'].items()
                if k in model.model_fields}) for r in receipt['results']]
    expected = cases(restored[0].start, restored[0].end,universe=universe)
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
        for market in ('spot','perp'):
            for symbol in cfg.weights:
                grouped[market+'/'+symbol]=[]
        for t in trades:
            grouped[t['market']+'/'+t['symbol']].append(t)
        results.append(dict(variant=item['variant'], summary=item['summary'],
                            trades=trade_stats(trades),
                            coin_market={k: {**trade_stats(v), **item['summary']['contributions'][k.replace('/',':')]}
                                         for k,v in sorted(grouped.items())},
                            rejected_off_trades=rejected_trades(off_trades, off_events, cfg.adx_threshold)))
    result = dict(research_only=True, research_on_seen_data=True, activation_allowed=False,
                  comparison_sha256=file_hash(source), dataset_checksum=next(iter(dataset)),
                  universe=universe, weights=restored[0].model_dump(mode='json')['weights'],
                  inputs_path=receipt['inputs_path'],inputs_sha256=receipt['inputs_sha256'],
                  results=results,
                  attribution_caveat='Conditional on ADX-off executed entries and sizing. Not exact counterfactual PnL: filters change entry timing, capital and subsequent positions.')
    lines = ['# A4 Donchian30/10 — ADX sensitivity 2022–2026', '',
             'Exploratory, already-seen data; not OOS confirmation or approval to trade.', '',
             '| ADX | Final USDT | Net USDT | Return % | DD % | Trades |',
             '|---|---:|---:|---:|---:|---:|']
    for r,c in zip(results, restored):
        s=r['summary']
        lines.append(f"| {c.adx_threshold if c.adx_threshold is not None else 'Off (DMI off)'} | {s['final_equity_known']:.2f} | {s['net_pnl']:.2f} | {s['net_return_pct']:.2f} | {s['max_drawdown_known_pct']:.2f} | {s['closed_trades']} |")
    lines += ['', '| Case | Fees | Slippage | Funding paid | Daily stops | DD days underwater | Recovered UTC |',
              '|---|---:|---:|---:|---:|---:|---|']
    for r in results:
        s=r['summary']; dd=s['drawdown_episode']
        lines.append(f"| {r['variant']} | {s['exchange_fee_known']:.2f} | {s['slippage_cost_known']:.2f} | {s['funding_paid_known']:.2f} | {s['daily_stops']} | {dd['days_underwater_observed']:.2f} | {dd['recovered_at'] or 'Not recovered by end'} |")
    if universe=='five':
        result['new_coin_contributions']={r['variant']:{s:sum(
            r['coin_market'][m+'/'+s]['net_pnl'] for m in ('spot','perp'))
            for s in ('NEARUSDT','ZECUSDT')} for r in results}
        lines += ['', '## NEAR and ZEC net contributions', '',
                  '| Case | NEAR USDT | ZEC USDT |', '|---|---:|---:|']
        for name,coins in result['new_coin_contributions'].items():
            lines.append(f"| {name} | {coins['NEARUSDT']:.2f} | {coins['ZECUSDT']:.2f} |")
        lines += ['', 'Positive contributions add profit; negative contributions reduce portfolio results.']
    if baseline_path is not None:
        baseline=read_inputs(baseline_path)
        if universe!='five' or baseline['window']!=receipt['window'] or len(baseline['results'])!=5:
            raise ValueError('comparison requires the same-window three-coin five-case baseline')
        comparisons=[]
        for item,new,(name,expected_cfg) in zip(baseline['results'],results,cases(restored[0].start,restored[0].end)):
            verify_item(item)
            if item['variant']!=name or item['config_checksum']!=fingerprint(expected_cfg.model_dump(mode='json')):
                raise ValueError('baseline is not the locked three-coin study')
            comparisons.append(dict(variant=name,three_final=item['summary']['final_equity_known'],
                five_final=new['summary']['final_equity_known'],
                delta_final=new['summary']['final_equity_known']-item['summary']['final_equity_known']))
        result['three_coin_comparison']=dict(path=str(Path(baseline_path).resolve()),sha256=file_hash(baseline_path),results=comparisons)
        lines += ['', '## Previous three-coin portfolio', '',
                  'ETH/SOL weights decrease from30% to20% within each sleeve. Differences cannot be attributed entirely to adding NEAR/ZEC.', '',
                  '| Case | Three final USDT | Five final USDT | Difference USDT |', '|---|---:|---:|---:|']
        for row in comparisons:
            lines.append(f"| {row['variant']} | {row['three_final']:.2f} | {row['five_final']:.2f} | {row['delta_final']:.2f} |")
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
                  '| Sleeve | Trades | Gross | Fees | Slippage | Funding paid | Net USDT | Win % |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        for k,v in r['coin_market'].items():
            win = f"{v['win_rate_pct']:.2f}" if v['win_rate_pct'] is not None else 'N/A'
            lines.append(f"| {k} | {v['closed_trades']} | {v['gross_pnl']:.2f} | {v['exchange_fee']:.2f} | {v['slippage_cost']:.2f} | {v['funding_paid']:.2f} | {v['net_pnl']:.2f} | {win} |")
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
              '1000USDT; Spot60/Short40; '+ '/'.join(s.removesuffix('USDT')+str(int(w*100)) for s,w in restored[0].weights.items())+' each. Short-only1x, realized-only sleeve reinvestment; no transfers.',
              'Donchian30/10 H4; EMA200/50; preceding VolumeMA20x1.2; approximate prior20day M15 volume profile retained.',
              'ATR14x3 H4 trailing, native M15 execution/risk; UTC daily loss3%, DD observe-only. No terminal DD halt.',
              'Enabled ADX: Wilder14, strictly above threshold and rising, directional DMI. Off disables both ADX/DMI.',
              'Per-fill Spot fee/slippage10/5bps; Perp5/5bps plus historical funding. No account fee verification.',
              'Continuous account/positions across October2024 join. 2026 and final half-year are partial.',
              ('Original source prices unchanged: eight old H4/M15 tuples plus five separately approved NEAR/ZEC tuples. Two added-coin H4 warmup interval views correct only closeTime; original raw rows remain intact. No generic tolerance.' if universe=='five' else
               'Original source prices unchanged: eight explicit H4/M15 price-view exceptions. No generic tolerance.'),
              'Per-coin Spot M15 availability, stale mark intervals and funding references retain their individually approved policies; no synthetic bars.',
              'DD uses observed known prices; OHLC cannot establish exact tick fills, book depth or liquidation. Daily3% is a trigger, not a guaranteed loss ceiling.',
              'No new thresholds chosen after results; no activation, official gate/champion change or VPS deployment.', '']
    lines += [f"Frozen input: `{receipt['inputs_path']}`; SHA256 `{receipt['inputs_sha256']}`.", '']
    if universe=='five':
        lines += ['The old source exceptions apply only to their original BTC/ETH/SOL series. The exact added-coin policy was separately approved and checksum-bound; further exceptions require separate approval.', '']
    root=Path(output_root).resolve();root.mkdir(parents=True,mode=0o700,exist_ok=False)
    files={name:_write(root/name,body) for name,body in (
        ('analysis.json',encoded(result)),('report.md','\n'.join(lines)))}
    _write(root/'manifest.json',encoded(dict(comparison_sha256=result['comparison_sha256'],files=files)))
    return result
