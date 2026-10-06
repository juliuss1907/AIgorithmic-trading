"""Verified isolated-account outcomes and descriptive setup 2 attribution."""

from datetime import datetime
from decimal import Decimal as D
import csv
from io import StringIO
from pathlib import Path
from statistics import median

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_adx_config import cases, config_type
from intraday.replay_v2.donchian_filter_data import verify_files
from intraday.replay_v2.donchian_oos_analysis import journal
from intraday.replay_v2.donchian_oos_pipeline import verify_item
from intraday.replay_v2.donchian_setups_analysis import outcomes, outcome_table
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


def verified_cases(path, universe):
    source = Path(path).resolve()
    receipt = read_inputs(source)
    if (receipt['preset'] != 'donchian-adx-'+universe or not receipt['research_only'] or
            not receipt['research_on_seen_data'] or receipt['activation_allowed'] or
            receipt['official_gate_eligible'] or not receipt['source_unchanged']):
        raise ValueError('requires an immutable research-only receipt')
    binding = read_inputs(source.parent/'binding.json')
    if fingerprint(binding) != receipt['binding_checksum'] or file_hash(receipt['inputs_path']) != receipt['inputs_sha256']:
        raise ValueError('research binding/input checksum changed')
    verify_files(receipt['source_files_sha256'])
    model = config_type(universe)
    window = [datetime.fromisoformat(receipt['window'][k]) for k in ('start', 'end')]
    expected = cases(*window, universe=universe)
    restored = [model.model_validate({k: v for k, v in r['config'].items() if k in model.model_fields})
                for r in receipt['results']]
    if [(r['variant'], c.model_dump(mode='json')) for r, c in zip(receipt['results'], restored)] != [
            (n, c.model_dump(mode='json')) for n, c in expected]:
        raise ValueError('locked case configuration changed')
    if binding['configs'] != {n: c.model_dump(mode='json') for n, c in expected}:
        raise ValueError('locked configuration binding changed')
    if len({r['dataset_checksum'] for r in receipt['results']}) != 1:
        raise ValueError('cases use different datasets')
    for item, cfg in zip(receipt['results'], restored):
        if (item['status'] != 'complete' or not item['deterministic_rerun_verified'] or
                item['config_checksum'] != fingerprint(cfg.model_dump(mode='json'))):
            raise ValueError('case is incomplete or configuration identity changed')
        verify_item(item)
    return receipt, restored, binding


def detailed_outcomes(trades):
    stats = outcomes(trades)
    hours = [(datetime.fromisoformat(t['closed_at'])-datetime.fromisoformat(t['opened_at'])).total_seconds()/3600
             for t in trades]
    wins = [t for t in trades if D(str(t['net_pnl'])) > 0]
    losses = [t for t in trades if D(str(t['net_pnl'])) < 0]
    return {**stats, 'holding_hours': dict(mean=sum(hours)/len(hours) if hours else None,
             median=median(hours) if hours else None, maximum=max(hours, default=None)),
            'largest_win': max(wins, key=lambda t: D(str(t['net_pnl'])), default=None),
            'largest_loss': min(losses, key=lambda t: D(str(t['net_pnl'])), default=None)}


def describe(comparison_path, output_root, *, baseline_path):
    receipt, configs, binding = verified_cases(comparison_path, 'single')
    baseline, baseline_configs, _ = verified_cases(baseline_path, 'setups')
    if (baseline['window'] != receipt['window'] or baseline['inputs_sha256'] != receipt['inputs_sha256'] or
            baseline['results'][1]['dataset_checksum'] != receipt['results'][0]['dataset_checksum']):
        raise ValueError('setup 2 comparison requires the same source/window/dataset')
    old_item, old_cfg = baseline['results'][1], baseline_configs[1]
    old_trades = journal(old_item, 'trades')
    results, samples, comparisons = [], [], []
    for item, cfg in zip(receipt['results'], configs):
        trades = journal(item, 'trades')
        if any(t['symbol'] != cfg.coin or t['market'] not in ('spot', 'perp') or
               t['side'] != ('long' if t['market'] == 'spot' else 'short') for t in trades):
            raise ValueError('trade belongs to another coin/market/direction')
        stats = detailed_outcomes(trades)
        if abs(stats['net_pnl']-item['summary']['net_pnl']) > 1e-7:
            raise ValueError('closed-trade outcomes do not reconcile with capital')
        groups = {m+'/'+cfg.coin: {**item['summary']['contributions'][m+':'+cfg.coin],
                  **detailed_outcomes([t for t in trades if t['market'] == m])} for m in ('spot', 'perp')}
        results.append(dict(variant=item['variant'], coin=cfg.coin, config=item['config'],
                            summary=item['summary'], trades=stats, coin_market=groups))
        samples.extend({'case': item['variant'], **t} for t in trades)
        old_stats = detailed_outcomes([t for t in old_trades if t['symbol'] == cfg.coin])
        initial = old_cfg.capital*old_cfg.weights[cfg.coin]
        comparisons.append(dict(coin=cfg.coin, setup_two_initial_allocation=float(initial),
            setup_two_contribution=old_stats,
            contribution_pct_initial_allocation=float(D(str(old_stats['net_pnl']))/initial*100),
            single_initial_capital=float(cfg.capital), single_net_pnl=stats['net_pnl'],
            single_return_pct=item['summary']['net_return_pct'], single_closed_trades=stats['closed_trades']))
    result = dict(research_only=True, research_on_seen_data=True, activation_allowed=False,
        comparison_sha256=file_hash(comparison_path), binding_checksum=receipt['binding_checksum'],
        engine_commit=binding['engine_commit'], inputs_path=receipt['inputs_path'], inputs_sha256=receipt['inputs_sha256'],
        dataset_checksum=receipt['results'][0]['dataset_checksum'], independent_accounts=True,
        initial_capital_per_account=1000, total_initial_capital=3000, results=results,
        baseline=dict(path=str(Path(baseline_path).resolve()), sha256=file_hash(baseline_path),
                      variant='Setup-2', summary=old_item['summary'], comparisons=comparisons),
        comparison_caveat='Setup 2 coin PnL divided by initial allocated capital is descriptive attribution, not an isolated account return. Independent reinvestment and daily guards change trades and sizing. Three isolated accounts start with3000USDT total, not a1000USDT portfolio.')
    lines = ['# Bộ 2 — chạy riêng SOL, ZEC và NEAR', '',
        f"Kỳ [{configs[0].start.date()}, {configs[0].end.date()}) UTC. Mỗi tài khoản1.000USDT, Spot600/Short400; tổng vốn ban đầu3.000USDT.",
        'Ba tài khoản độc lập; không reset vốn/vị thế. Dữ liệu đã xem, không phải OOS mới hoặc trading approval.', '',
        '| Coin | Vốn cuối | Net USDT | Return % | DD % | Lệnh | Phí | Slippage | Funding paid | Daily stops |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in results:
        s = r['summary']
        lines.append(f"| {r['coin']} | {s['final_equity_known']:.2f} | {s['net_pnl']:.2f} | {s['net_return_pct']:.2f} | "
            f"{s['max_drawdown_known_pct']:.2f} | {s['closed_trades']} | {s['exchange_fee_known']:.2f} | "
            f"{s['slippage_cost_known']:.2f} | {s['funding_paid_known']:.2f} | {s['daily_stops']} |")
    lines += ['', '## Thắng/thua sau chi phí', '']+outcome_table({r['coin']: r['trades'] for r in results})
    lines += ['', '## So với đóng góp trong bộ 2', '', result['comparison_caveat'], '',
              '| Coin | Vốn phân bổ cũ | Net đóng góp cũ | Net/vốn phân bổ cũ % | Lệnh cũ | Vốn riêng | Return riêng % | Lệnh riêng |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for c in comparisons:
        lines.append(f"| {c['coin']} | {c['setup_two_initial_allocation']:.0f} | {c['setup_two_contribution']['net_pnl']:.2f} | "
            f"{c['contribution_pct_initial_allocation']:.2f} | {c['setup_two_contribution']['closed_trades']} | "
            f"{c['single_initial_capital']:.0f} | {c['single_return_pct']:.2f} | {c['single_closed_trades']} |")
    for r in results:
        s, stats = r['summary'], r['trades']
        lines += ['', '## '+r['coin'], '', '### Spot / Short', '']+outcome_table(r['coin_market'])
        lines += ['', '| Phần | Gross USDT | Phí | Slippage | Funding paid | Net USDT |', '|---|---:|---:|---:|---:|---:|']
        for name, v in r['coin_market'].items():
            lines.append(f"| {name} | {v['gross_pnl']:.2f} | {v['exchange_fee']:.2f} | {v['slippage_cost']:.2f} | {v['funding_paid']:.2f} | {v['net_pnl']:.2f} |")
        lines += ['', 'Giữ lệnh (giờ): '+encoded(stats['holding_hours']),
                  '', 'Lệnh thắng lớn nhất (net, UTC): '+encoded(stats['largest_win']),
                  '', 'Lệnh thua lớn nhất (net, UTC): '+encoded(stats['largest_loss']),
                  '', 'DD sâu nhất và hồi phục: '+encoded(s['drawdown_episode']),
                  '', '### Theo năm — marked equity', '',
                  '| Năm | Vốn đầu | Vốn cuối | Thay đổi USDT | Return % |', '|---|---:|---:|---:|---:|']
        for year, v in s['annual_marked_equity'].items():
            lines.append(f"| {year} | {v['start_equity_usdt']:.2f} | {v['end_equity_usdt']:.2f} | {v['marked_change_usdt']:.2f} | {v['return_pct']:.2f} |")
        lines += ['', '### Theo nửa năm', '', '| Kỳ UTC | Đủ nửa năm | PnL USDT | Return % | DD % |', '|---|---|---:|---:|---:|']
        for v in s['six_month_periods']:
            lines.append(f"| {v['start'][:10]} → {v['end'][:10]} | {v['full_calendar_half']} | {v['pnl']:.2f} | {v['return_pct']:.2f} | {v['max_drawdown_pct']:.2f} |")
    lines += ['', '## Quy tắc và dữ liệu', '',
        'ADX Wilder14 >20 và tăng, DMI đúng hướng; Donchian30/10 H4, EMA200/50, VolumeMA20×1,2 và volume profile giữ nguyên.',
        'ATR14×3, sizing min(1,0,02/ATR%) và trailing theo H4; M15 thực thi/risk. Short-only1x, realized-only riêng từng Spot/Short.',
        'Daily loss3% theo từng tài khoản; ngày UTC kế tiếp AND flat mới resume. DD chỉ theo dõi; không chuyển vốn giữa coin.',
        'Spot fee/slippage10/5bps, Perp5/5bps mỗi fill và funding lịch sử; funding paid âm là khoản nhận. Chưa đổi sang limit order.',
        'Dùng nguyên manifest5coin và ngoại lệ đã duyệt; chuẩn bị cả5coin cùng checksum, chỉ giao dịch/định giá coin được chọn.',
        'Giữ raw rows, giá/rate/time và provenance; gap dùng quan sát cuối có stale, chờ nến thật; không tạo nến/giá giả.',
        'OHLC không xác định được tick/queue/depth; DD theo giá quan sát; daily3% có thể overshoot do gap và phí đóng.',
        'Năm2026 và nửa năm cuối chưa đủ kỳ. Phân tích năm/nửa năm dùng marked equity; thắng/thua dùng net lệnh đã đóng.',
        'Mỗi case lặp cơ học sau serialization; khớp summary, methodology, result ID và cả3journal.',
        'Không cộng DD của ba tài khoản hoặc trình bày chúng như portfolio1.000USDT. Không push/merge/deploy/gọi LLM/kích hoạt trading.', '',
        f"Input: `{receipt['inputs_path']}`; SHA256 `{receipt['inputs_sha256']}`.",
        f"Dataset: `{result['dataset_checksum']}`. Commit: `{binding['engine_commit']}`.", '']
    root = Path(output_root).resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    stream = StringIO()
    fields = ['case', 'symbol', 'market', 'side', 'opened_at', 'closed_at', 'entry_price',
              'exit_price', 'quantity', 'exit_reason', 'gross_pnl', 'exchange_fee', 'slippage_cost', 'funding_paid', 'net_pnl']
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader(); writer.writerows(samples)
    files = {name: _write(root/name, body) for name, body in (
        ('analysis.json', encoded(result)), ('report.md', '\n'.join(lines)), ('trade-samples.csv', stream.getvalue()))}
    _write(root/'manifest.json', encoded(dict(comparison_sha256=result['comparison_sha256'],
                                             baseline_sha256=result['baseline']['sha256'], files=files)))
    return result
