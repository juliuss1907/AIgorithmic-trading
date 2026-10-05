"""Verified descriptive samples for the five requested portfolio setups."""

from collections import defaultdict
import csv
from decimal import Decimal as D
from io import StringIO
from pathlib import Path

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_adx_config import cases
from intraday.replay_v2.donchian_adx_setups import ADXSetupConfig, allocation_text, market_weights
from intraday.replay_v2.donchian_filter_data import verify_files
from intraday.replay_v2.donchian_oos_analysis import journal, trade_stats
from intraday.replay_v2.donchian_oos_pipeline import verify_item
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


def outcomes(trades):
    values = [D(str(t['net_pnl'])) for t in trades]
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    winning, losing = sum(wins, D(0)), sum(losses, D(0))
    return {**trade_stats(trades), 'breakeven': sum(v == 0 for v in values),
            'winning_net_sum': float(winning), 'losing_net_sum': float(losing),
            'mean_win': float(winning/len(wins)) if wins else None,
            'mean_loss': float(losing/len(losses)) if losses else None,
            'profit_factor': float(winning/-losing) if losing else None}


def outcome_table(groups):
    lines = ['| Bộ / Coin | Lệnh | Thắng | Thua | Win % | Tổng lãi | Tổng lỗ | Net USDT | PF |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, s in groups.items():
        win = f"{s['win_rate_pct']:.2f}" if s['win_rate_pct'] is not None else 'N/A'
        pf = f"{s['profit_factor']:.2f}" if s['profit_factor'] is not None else 'N/A'
        lines.append(f"| {name} | {s['closed_trades']} | {s['wins']} | {s['losses']} | {win} | "
                     f"{s['winning_net_sum']:.2f} | {s['losing_net_sum']:.2f} | {s['net_pnl']:.2f} | {pf} |")
    return lines


def describe(comparison_path, output_root):
    source = Path(comparison_path).resolve()
    receipt = read_inputs(source)
    if (receipt['preset'] != 'donchian-adx-setups' or not receipt['research_on_seen_data'] or
            receipt['activation_allowed'] or receipt['official_gate_eligible'] or not receipt['source_unchanged']):
        raise ValueError('requires a verified research-only setups receipt')
    binding = read_inputs(source.parent/'binding.json')
    if fingerprint(binding) != receipt['binding_checksum'] or file_hash(receipt['inputs_path']) != receipt['inputs_sha256']:
        raise ValueError('research source binding changed')
    verify_files(receipt['source_files_sha256'])
    restored = [ADXSetupConfig.model_validate({k: v for k, v in r['config'].items()
                if k in ADXSetupConfig.model_fields}) for r in receipt['results']]
    expected = cases(restored[0].start, restored[0].end, universe='setups')
    if [(r['variant'], c.model_dump(mode='json')) for r, c in zip(receipt['results'], restored)] != [
            (n, c.model_dump(mode='json')) for n, c in expected]:
        raise ValueError('requested five setups changed')
    datasets = {r['dataset_checksum'] for r in receipt['results']}
    if len(datasets) != 1:
        raise ValueError('setups must use the same validated source dataset')
    results, samples = [], []
    for item, cfg in zip(receipt['results'], restored):
        verify_item(item)
        trades = journal(item, 'trades')
        for t in trades:
            if t['symbol'] not in market_weights(cfg, t['market']):
                raise ValueError('trade belongs to an unallocated market/coin')
            if t['side'] != ('long' if t['market'] == 'spot' else 'short'):
                raise ValueError('unexpected trade direction')
            samples.append({'setup': item['variant'], **t})
        stats = outcomes(trades)
        if abs(stats['net_pnl']-item['summary']['net_pnl']) > 1e-7:
            raise ValueError('closed-trade outcomes do not reconcile with capital')
        groups = {m+'/'+s: [] for m in ('spot', 'perp') for s in market_weights(cfg, m)}
        coins = defaultdict(list)
        for t in trades:
            groups[t['market']+'/'+t['symbol']].append(t)
            coins[t['symbol']].append(t)
        coin_stats = {s: outcomes(coins[s]) for s in sorted(set(cfg.weights) | set(market_weights(cfg, 'perp')))}
        results.append(dict(variant=item['variant'], config=item['config'], summary=item['summary'],
                            trades=stats, coin=coin_stats,
                            coin_market={k: {**outcomes(v), **item['summary']['contributions'][k.replace('/', ':')]}
                                         for k, v in groups.items()}))
    result = dict(research_only=True, research_on_seen_data=True, activation_allowed=False,
                  comparison_sha256=file_hash(source), binding_checksum=receipt['binding_checksum'],
                  inputs_path=receipt['inputs_path'], inputs_sha256=receipt['inputs_sha256'],
                  dataset_checksum=next(iter(datasets)), results=results)
    lines = ['# A4 ADX — 5 setup phân bổ portfolio', '',
             f'Kỳ [{restored[0].start.date()}, {restored[0].end.date()}) UTC; 1.000 USDT liên tục, không reset vốn/vị thế.',
             'Dữ liệu lịch sử đã xem; đây là thu thập mẫu và phân tích độ nhạy, không phải OOS mới hay trading approval.', '',
             '| Bộ | Vốn cuối | Net USDT | Return % | DD % | Lệnh | Phí | Slippage | Funding paid | Daily stops |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in results:
        s = r['summary']
        lines.append(f"| {r['variant']} | {s['final_equity_known']:.2f} | {s['net_pnl']:.2f} | {s['net_return_pct']:.2f} | "
                     f"{s['max_drawdown_known_pct']:.2f} | {s['closed_trades']} | {s['exchange_fee_known']:.2f} | "
                     f"{s['slippage_cost_known']:.2f} | {s['funding_paid_known']:.2f} | {s['daily_stops']} |")
    lines += ['', '## Lệnh thắng/thua sau chi phí', '']+outcome_table({r['variant']: r['trades'] for r in results})
    for r, cfg in zip(results, restored):
        lines += ['', '## '+r['variant'], '', allocation_text(cfg), '',
                  '### Thắng/thua theo coin (Spot + Short)', '']+outcome_table(r['coin'])
        lines += ['', '### Theo coin × Spot/Short', '']+outcome_table(r['coin_market'])
        lines += ['', '| Phần | Gross USDT | Phí | Slippage | Funding paid | Net USDT |',
                  '|---|---:|---:|---:|---:|---:|']
        for k, s in r['coin_market'].items():
            lines.append(f"| {k} | {s['gross_pnl']:.2f} | {s['exchange_fee']:.2f} | {s['slippage_cost']:.2f} | {s['funding_paid']:.2f} | {s['net_pnl']:.2f} |")
        lines += ['', '### Theo năm — thay đổi marked equity', '',
                  '| Năm | Vốn đầu | Vốn cuối | Thay đổi USDT | Return % |', '|---|---:|---:|---:|---:|']
        for y, s in r['summary']['annual_marked_equity'].items():
            lines.append(f"| {y} | {s['start_equity_usdt']:.2f} | {s['end_equity_usdt']:.2f} | {s['marked_change_usdt']:.2f} | {s['return_pct']:.2f} |")
        lines += ['', '### Theo nửa năm', '', '| Kỳ UTC | Đủ nửa năm | PnL USDT | Return % | DD % |',
                  '|---|---|---:|---:|---:|']
        for s in r['summary']['six_month_periods']:
            lines.append(f"| {s['start'][:10]} → {s['end'][:10]} | {s['full_calendar_half']} | {s['pnl']:.2f} | {s['return_pct']:.2f} | {s['max_drawdown_pct']:.2f} |")
        lines += ['', 'DD sâu nhất và hồi phục: '+encoded(r['summary']['drawdown_episode'])]
    lines += ['', '## Quy tắc và provenance', '',
              'Bộ 1: 650/350 là vốn ban đầu, tái đầu tư chung theo Spot/Short; không mở tài khoản nhóm riêng.',
              'Bộ 3: SOL/ZEC/NEAR4/3/3, Spot100%; không mở Perp, margin và funding bằng0.',
              'Tỷ lệ1:1:1 dùng Decimal28 chữ số; coin cuối nhận phần dư tối đa1e-28 để tổng tỷ trọng chính xác1.',
              'Donchian30/10 H4; EMA200/50; VolumeMA20×1,2; volume profile20 ngày; ATR14×3 và sizing ATR giữ nguyên.',
              'ADX Wilder14 > ngưỡng và tăng; DMI đúng hướng. Ngưỡng theo map từng coin/phần thị trường trong config.',
              'Short-only1x; Spot/Perp tái đầu tư riêng realized-only; không chuyển vốn hoặc rebalance vị thế đang mở.',
              'Daily loss3% toàn portfolio; ngày UTC kế tiếp AND flat mới resume. DD chỉ theo dõi, không terminal halt.',
              'Phí/slippage mỗi fill: Spot10/5bps, Perp5/5bps; funding lịch sử thực tế. Funding paid âm là khoản nhận.',
              'Nguồn5coin, snapshot/SHA256 và các ngoại lệ nguồn đã duyệt được tái sử dụng nguyên vẹn; không thêm ngoại lệ.',
              'Chuẩn bị cả5coin để cùng checksum; giao dịch/valuation chỉ trên coin/phần thị trường được phân bổ.',
              'Mỗi case lặp cơ học và khớp summary, methodology, result ID cùng toàn bộ3journal.',
              'PnL theo năm/nửa năm là marked equity; thắng/thua dựa trên lệnh đã đóng, sau mọi chi phí.',
              'Không coi chênh lệch giữa setup là hiệu ứng riêng của ADX: universe, tỷ trọng và phần thị trường cùng thay đổi.',
              'Nguồn đã có gap/partial close được duyệt: định giá stale và chờ nến thật; không tạo nến/giá giả.',
              'OHLC không xác định được fills/DD chính xác theo tick; daily3% có thể overshoot do gap và closing costs.',
              'Không push/merge/deploy/gọi LLM/kích hoạt trading.', '',
              f"Nguồn: `{receipt['inputs_path']}`; SHA256 `{receipt['inputs_sha256']}`.",
              f"Commit runner đã khóa: `{binding['engine_commit']}`.", '']
    root = Path(output_root).resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    stream = StringIO()
    fields = ['setup', 'symbol', 'market', 'side', 'opened_at', 'closed_at', 'entry_price',
              'exit_price', 'quantity', 'exit_reason', 'gross_pnl', 'exchange_fee', 'slippage_cost', 'funding_paid', 'net_pnl']
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(samples)
    files = {name: _write(root/name, body) for name, body in (
        ('analysis.json', encoded(result)), ('report.md', '\n'.join(lines)), ('trade-samples.csv', stream.getvalue()))}
    _write(root/'manifest.json', encoded(dict(comparison_sha256=result['comparison_sha256'], files=files)))
    return result
