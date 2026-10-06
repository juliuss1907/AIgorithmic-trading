"""Descriptive analysis of existing Setup-2 journals; never simulates trades."""

from datetime import datetime, timezone
from decimal import Decimal as D
from hashlib import sha256
from statistics import median

ZERO = D(0)
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 2, tzinfo=timezone.utc)
PAIRS = tuple(f'{s}/{m}' for s in ('SOLUSDT', 'ZECUSDT', 'NEARUSDT')
              for m in ('spot', 'perp'))
# Fixed before outcomes. Lower endpoints are exclusive; upper endpoints inclusive.
BINS = {
    'adx': ('20', '25', '35'),
    'adx_rise': ('0', '1', '3'),
    'atr_pct': ('0', '2', '4'),
    'volume_ratio': ('1.2', '2', '3'),
    'ema50_distance_atr': ('0', '1', '2'),
}
POLICY = dict(version=1, bins=BINS, bin_rule='lower < value <= upper',
              scopes=['portfolio', *PAIRS], complete_years=[2022, 2023, 2024, 2025],
              minimum_group=20, minimum_year=5, minimum_negative_years=3,
              candidate_rule='mean net R < 0 overall and in >=3 complete years',
              tails=[1, 5, 10, 20], tail_bases=['net_pnl', 'net_r'],
              overlap_basis='global top 10 positive net_pnl winners',
              period_attribution='full closed net attributed to UTC entry period',
              r_denominator='quantity * abs(entry_price - entry_features.stop)',
              r_denominator_excludes_costs=True, descriptive_only=True)


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing/non-numeric value')
    result = D(str(value))
    if not result.is_finite():
        raise ValueError('non-finite number')
    return result


def instant(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('timezone required')
    return dt.astimezone(timezone.utc)


def close(a, b, tolerance=D('1e-18')):
    if abs(a-b) > tolerance:
        raise ValueError(f'identity mismatch: {a} != {b}')


def band(feature, value):
    floor, first, second = map(D, BINS[feature])
    if not value.is_finite() or value <= floor:
        raise ValueError(f'{feature} outside locked bins: {value}')
    if value <= first:
        return f'({floor},{first}]'
    if value <= second:
        return f'({first},{second}]'
    return f'>{second}'


def join_trades(trades, events):
    """Require one causal feature event per closed trade, with no orphan entries."""
    entries = {}
    for e in events:
        if e['kind'] != 'entry_features':
            continue
        key = (instant(e['at']), e['market'], e['symbol'])
        if key in entries:
            raise ValueError('duplicate entry features')
        entries[key] = e
    rows, seen = [], set()
    for t in trades:
        opened, closed = instant(t['opened_at']), instant(t['closed_at'])
        key = (opened, t['market'], t['symbol'])
        if key in seen or key not in entries:
            raise ValueError('duplicate trade or missing entry features')
        seen.add(key)
        e, f = entries[key], entries[key]['features']
        pair = t['symbol']+'/'+t['market']
        side = 1 if t['market'] == 'spot' else -1
        if pair not in PAIRS or t['side'] != ('long' if side == 1 else 'short'):
            raise ValueError('wrong coin/market/direction')
        if not START <= opened < END or not opened <= closed <= END:
            raise ValueError('trade outside locked window')
        available = instant(e['signal_available_at'])
        if available > opened:
            raise ValueError('non-causal entry features')
        names = ('entry_price', 'exit_price', 'quantity', 'gross_pnl', 'exchange_fee',
                 'slippage_cost', 'funding_paid', 'net_pnl')
        row = {name: number(t[name]) for name in names}
        entry, exit_price, qty = (row[n] for n in names[:3])
        atr, signal_close, ema50, volume, volume_ma = (number(f[n]) for n in
            ('atr', 'close', 'ema50', 'volume', 'volume_ma'))
        stop = number(e['stop'])
        if min(entry, exit_price, qty, atr, signal_close, ema50, volume, volume_ma, stop) <= 0:
            raise ValueError('nonpositive price/quantity/feature')
        if min(row['exchange_fee'], row['slippage_cost']) < 0:
            raise ValueError('negative fee/slippage')
        distance = side*(entry-stop)
        if distance <= 0:
            raise ValueError('initial stop on wrong side')
        close(distance, 3*atr)
        risk = qty*distance
        close(row['gross_pnl'], side*qty*(exit_price-entry))
        cost = row['exchange_fee']+row['slippage_cost']+row['funding_paid']
        close(row['net_pnl'], row['gross_pnl']-cost)
        features = dict(adx=number(f['adx']), adx_rise=number(f['adx'])-number(f['prior_adx']),
            atr_pct=atr/signal_close*100, volume_ratio=volume/volume_ma,
            ema50_distance_atr=side*(signal_close-ema50)/atr)
        bins = {n+'_band': band(n, v) for n, v in features.items()}
        delta = closed-opened
        microseconds = (delta.days*86400+delta.seconds)*1000000+delta.microseconds
        trade_id = sha256(f'{opened.isoformat()}|{t["market"]}|{t["symbol"]}'.encode()).hexdigest()
        rows.append(dict(row, trade_id=trade_id, pair=pair, symbol=t['symbol'], market=t['market'],
            side=t['side'], opened_at=opened.isoformat(), closed_at=closed.isoformat(),
            signal_available_at=available.isoformat(), entry_year=opened.year,
            entry_half=f'{opened.year}-H{1 if opened.month <= 6 else 2}',
            exit_reason=t['exit_reason'], signal_close=signal_close, atr=atr, ema50=ema50,
            volume=volume, volume_ma=volume_ma, prior_adx=number(f['prior_adx']), initial_stop=stop,
            entry_notional=qty*entry, initial_risk_usdt=risk, cost_usdt=cost,
            gross_r=row['gross_pnl']/risk, net_r=row['net_pnl']/risk, cost_r=cost/risk,
            holding_hours=D(microseconds)/D(3600000000), **features, **bins))
    if seen != set(entries):
        raise ValueError('orphan entry features')
    return sorted(rows, key=lambda r: (r['opened_at'], r['market'], r['symbol']))


def ranked_winners(rows, basis):
    return sorted((r for r in rows if r['net_pnl'] > 0),
                  key=lambda r: (-r[basis], r['trade_id']))


def stats(rows, top_ids=frozenset()):
    n = len(rows)
    result = dict(count=n, wins=sum(r['net_pnl'] > 0 for r in rows),
                  losses=sum(r['net_pnl'] < 0 for r in rows),
                  breakeven=sum(r['net_pnl'] == 0 for r in rows), low_sample=n < 20)
    for field in ('gross_pnl', 'net_pnl', 'exchange_fee', 'slippage_cost', 'funding_paid',
                  'cost_usdt', 'gross_r', 'net_r', 'cost_r'):
        result[field] = sum((r[field] for r in rows), ZERO)
    positive = sum((r['net_pnl'] for r in rows if r['net_pnl'] > 0), ZERO)
    negative = sum((r['net_pnl'] for r in rows if r['net_pnl'] < 0), ZERO)
    positive_r = sum((r['net_r'] for r in rows if r['net_r'] > 0), ZERO)
    negative_r = sum((r['net_r'] for r in rows if r['net_r'] < 0), ZERO)
    overlap = [r for r in rows if r['trade_id'] in top_ids]
    result.update(mean_net_r=result['net_r']/n if n else None,
        mean_net_pnl=result['net_pnl']/n if n else None,
        win_rate_pct=D(result['wins'])/n*100 if n else None,
        mean_win=positive/result['wins'] if result['wins'] else None,
        mean_loss=negative/result['losses'] if result['losses'] else None,
        profit_factor=positive/-negative if negative else None,
        profit_factor_r=positive_r/-negative_r if negative_r else None,
        positive_net_usdt=positive, negative_net_usdt=negative,
        positive_net_r=positive_r, negative_net_r=negative_r,
        mean_holding_hours=sum((r['holding_hours'] for r in rows), ZERO)/n if n else None,
        median_holding_hours=median([r['holding_hours'] for r in rows]) if n else None,
        global_top10_count=len(overlap),
        global_top10_net_usdt=sum((r['net_pnl'] for r in overlap), ZERO),
        global_top10_net_r=sum((r['net_r'] for r in overlap), ZERO),
        global_top10_ids=[r['trade_id'] for r in overlap])
    result['gross_wins'] = sum(r['gross_pnl'] > 0 for r in rows)
    result['gross_win_to_net_loss'] = sum(r['gross_pnl'] > 0 and r['net_pnl'] < 0 for r in rows)
    for label, selected in (('winners', [r for r in rows if r['net_pnl'] > 0]),
                            ('losers', [r for r in rows if r['net_pnl'] < 0])):
        for field in ('exchange_fee', 'slippage_cost', 'funding_paid', 'cost_usdt'):
            result[label+'_'+field] = sum((r[field] for r in selected), ZERO)
    return result


def needs_review(overall, years):
    negative = [y for y in POLICY['complete_years'] if
                years[str(y)]['count'] >= POLICY['minimum_year'] and
                years[str(y)]['mean_net_r'] < 0]
    eligible = (overall['count'] >= POLICY['minimum_group'] and
                overall['mean_net_r'] < 0 and len(negative) >= POLICY['minimum_negative_years'])
    return dict(needs_review=eligible, qualifying_negative_years=negative)


def analyze(rows):
    """Independent one-variable partitions, fixed entry cohorts, and descriptive tails."""
    top_ids = {r['trade_id'] for r in ranked_winners(rows, 'net_pnl')[:10]}
    tails = {}
    for basis in POLICY['tail_bases']:
        ranked = ranked_winners(rows, basis)
        tails[basis] = []
        for k in POLICY['tails']:
            chosen = ranked[:k]
            ids = {r['trade_id'] for r in chosen}
            tails[basis].append(dict(k=k, selected_count=len(chosen),
                trade_ids=[r['trade_id'] for r in chosen], selected=stats(chosen, top_ids),
                remaining=stats([r for r in rows if r['trade_id'] not in ids], top_ids)))
    groups = []
    for scope in POLICY['scopes']:
        scoped = [r for r in rows if scope == 'portfolio' or r['pair'] == scope]
        partitions = [('all', 'all', scoped)]
        for feature, (lower, middle, upper) in BINS.items():
            for label in (f'({lower},{middle}]', f'({middle},{upper}]', f'>{upper}'):
                partitions.append((feature, label, [r for r in scoped if r[feature+'_band'] == label]))
        for feature, label, subset in partitions:
            overall = stats(subset, top_ids)
            years = {str(y): dict(stats([r for r in subset if r['entry_year'] == y], top_ids),
                         complete_period=y < 2026) for y in range(2022, 2027)}
            halves = {f'{y}-H{h}': dict(stats([r for r in subset if r['entry_half'] == f'{y}-H{h}'], top_ids),
                        complete_period=(y, h) != (2026, 2))
                      for y in range(2022, 2027) for h in (1, 2)}
            for cohorts in (years, halves):
                if sum(v['count'] for v in cohorts.values()) != len(subset):
                    raise ValueError('cohort count reconciliation failed')
                close(sum((v['net_pnl'] for v in cohorts.values()), ZERO), overall['net_pnl'])
            groups.append(dict(group_id=f'{scope}|{feature}|{label}', scope=scope,
                feature=feature, band=label, trade_ids=[r['trade_id'] for r in subset],
                overall=overall, years=years, halves=halves, **needs_review(overall, years)))
        for feature in BINS:
            partition = [g for g in groups if g['scope'] == scope and g['feature'] == feature]
            if sum(g['overall']['count'] for g in partition) != len(scoped):
                raise ValueError('bin count reconciliation failed')
            for field in ('net_pnl', 'cost_usdt', 'net_r'):
                close(sum((g['overall'][field] for g in partition), ZERO), stats(scoped)[field])
    overall = stats(rows, top_ids)
    for field in ('net_pnl', 'cost_usdt', 'net_r'):
        close(sum((g['overall'][field] for g in groups if g['feature'] == 'all' and
                   g['scope'] != 'portfolio'), ZERO), overall[field])
    eligible = sorted((g for g in groups if g['needs_review']),
                      key=lambda g: (g['overall']['mean_net_r'], g['overall']['net_pnl'], g['group_id']))
    candidates = [g['group_id'] for g in eligible]
    return dict(overall=overall, tails=tails, groups=groups, candidates=candidates)


def csv_text(rows):
    import csv
    from io import StringIO
    import json
    stream = StringIO()
    fields = list(rows[0]) if rows else []
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
                         for k, v in row.items()})
    return stream.getvalue()


def export_groups(result):
    rows = []
    for group in result['groups']:
        metadata = {n: group[n] for n in ('group_id', 'scope', 'feature', 'band',
                                         'needs_review', 'qualifying_negative_years')}
        rows.append(dict(metadata, period_type='overall', period='all', complete_period=False,
                         **group['overall']))
        for kind in ('years', 'halves'):
            rows.extend(dict(metadata, period_type=kind, period=period, **s)
                        for period, s in group[kind].items())
    return rows


def source_snapshot(summary_path):
    """Hash all 18 published reports and their comparison receipts without changing them."""
    import json
    from pathlib import Path
    from intraday.replay_v2.portfolio_study import file_hash
    summary_path = Path(summary_path).resolve()
    summary = json.loads(summary_path.read_text())
    if summary['case_count'] != 18 or len(summary['rows']) != 18:
        raise ValueError('requires original 18-case inventory')
    paths = {summary_path, summary_path.with_suffix('.md'), summary_path.with_suffix('.csv'),
             summary_path.parent/'manifest.json'}
    for source in summary['sources']:
        path = Path(source['comparison_path'])
        if file_hash(path) != source['comparison_sha256']:
            raise ValueError('legacy comparison changed')
        paths.update((path, path.parent/'binding.json'))
    for row in summary['rows']:
        root = Path(row['report_directory'])
        manifest = json.loads((root/'manifest.json').read_text())
        paths.add(root/'manifest.json')
        for name, advertised in manifest['files'].items():
            path = root/name
            if path.parent != root or file_hash(path) != advertised['sha256']:
                raise ValueError('legacy report journal changed')
            paths.add(path)
    return {str(p): file_hash(p) for p in sorted(paths)}


def verify_snapshot(snapshot):
    from intraday.replay_v2.portfolio_study import file_hash
    for path, expected in snapshot.items():
        if file_hash(path) != expected:
            raise ValueError(f'frozen source changed: {path}')


def freeze(comparison_path, root, inventory_path):
    """Write the immutable binding before reading/aggregating trade outcomes."""
    import json
    import platform
    from pathlib import Path
    from intraday.replay_v2.artifacts import _write
    from intraday.replay_v2.donchian_single_analysis import verified_cases
    from intraday.replay_v2.donchian_oos_pipeline import git, source_hashes
    from intraday.replay_v2.metrics import encoded, fingerprint
    from intraday.replay_v2.portfolio_study import file_hash
    repo = Path(__file__).resolve().parents[1]
    comparison_path, root = Path(comparison_path).resolve(), Path(root).resolve()
    if root.exists():
        raise ValueError('report root already exists; no overwrite allowed')
    if git('status', '--porcelain', repo=repo):
        raise ValueError('commit diagnostic source before freezing')
    receipt, _, engine_binding = verified_cases(comparison_path, 'setups')
    item = receipt['results'][1]
    if (item['variant'] != 'Setup-2' or instant(receipt['window']['start']) != START or
            instant(receipt['window']['end']) != END):
        raise ValueError('requires exact Setup-2 window')
    inventory = json.loads(Path(inventory_path).read_text())
    if not any(r['result_id'] == item['result_id'] for r in inventory['rows']):
        raise ValueError('setup 2 not found in legacy inventory')
    snapshot = source_snapshot(inventory_path)
    snapshot[str(comparison_path)] = file_hash(comparison_path)
    snapshot[str(Path(receipt['inputs_path']))] = file_hash(receipt['inputs_path'])
    analysis_paths = [Path(__file__).resolve(), repo/'tests/test_donchian_setup2_diagnostics.py',
                      repo/'docs/donchian-setup2-diagnostics.md']
    analysis_hashes = {str(p): file_hash(p) for p in analysis_paths}
    engine_hashes = {str(repo/path): checksum for path, checksum in source_hashes(repo).items()}
    binding = dict(research_only=True, activation_allowed=False, new_strategy_runs=0,
        frozen_at=datetime.now(timezone.utc).isoformat(), analysis_commit=git('rev-parse', 'HEAD', repo=repo),
        python=platform.python_version(), policy_checksum=fingerprint(POLICY),
        source_comparison=str(comparison_path), config_checksum=item['config_checksum'],
        dataset_checksum=item['dataset_checksum'], result_id=item['result_id'],
        source_engine_commit=engine_binding['engine_commit'], config=item['config'],
        source_files_sha256=receipt['source_files_sha256'], source_snapshot_sha256=snapshot,
        analysis_files_sha256=analysis_hashes, current_engine_files_sha256=engine_hashes)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    root.chmod(0o700)
    _write(root/'grouping-config.json', encoded(POLICY))
    _write(root/'binding.json', encoded(binding))
    return binding, item


def reconcile_baseline(result, item):
    s = result['overall']
    if (s['count'], s['wins'], s['losses'], s['breakeven']) != (419, 177, 242, 0):
        raise ValueError('original 419-trade outcomes changed')
    close(s['net_pnl'], D('663.9825162550667'), D('1e-7'))
    close(s['cost_usdt'], D('174.51370959896636'), D('1e-7'))
    for field, key in (('net_pnl', 'net_pnl'), ('exchange_fee', 'exchange_fee_known'),
                       ('slippage_cost', 'slippage_cost_known'), ('funding_paid', 'funding_paid_known')):
        close(s[field], number(item['summary'][key]), D('1e-7'))
    for group in result['groups']:
        if group['feature'] != 'all' or group['scope'] == 'portfolio':
            continue
        symbol, market = group['scope'].split('/')
        original = item['summary']['contributions'][market+':'+symbol]
        if group['overall']['count'] != original['closed_trades']:
            raise ValueError('coin/market count changed')
        for field in ('net_pnl', 'gross_pnl', 'exchange_fee', 'slippage_cost', 'funding_paid'):
            close(group['overall'][field], number(original[field]), D('1e-7'))


def format_number(value, digits=3):
    return '—' if value is None else f'{value:.{digits}f}'


def group_table(groups):
    lines = ['| Nhóm | Lệnh | Thắng/thua | Net USDT | Mean net R | PF USDT | Chi phí USDT | Top10 USDT: số/net |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for g in groups:
        s = g['overall']
        label = g['group_id']+(' (mẫu ít)' if s['low_sample'] else '')
        lines.append(f"| {label} | {s['count']} | {s['wins']}/{s['losses']} | "
            f"{format_number(s['net_pnl'])} | {format_number(s['mean_net_r'])} | "
            f"{format_number(s['profit_factor'])} | {format_number(s['cost_usdt'])} | "
            f"{s['global_top10_count']}/{format_number(s['global_top10_net_usdt'])} |")
    return lines


def markdown(result, binding):
    s = result['overall']
    lines = ['# Chẩn đoán bộ 2: lệnh thua, chi phí và lệnh thắng lớn', '',
        'SOL/ZEC/NEAR = 4:3:3; ADX20 tăng + DMI đúng hướng; Spot60% / Short40%, Short-only1x. '
        'Vốn ban đầu1.000USDT, tái đầu tư realized-only chung theo từng phần Spot/Short, không reset.', '',
        'Kỳ [2022-01-01, 2026-10-02) UTC. Giữ Donchian30/10 H4, EMA200/50, '
        'VolumeMA20×1,2, volume profile, ATR14×3 và sizing ATR. M15 thực thi/risk; daily loss3% toàn portfolio; '
        'DD chỉ theo dõi. Đây là phân tích nhật ký đã có; không chạy chiến lược mới.', '',
        f"419 lệnh: **177 thắng /242 thua**, net **{s['net_pnl']:.2f}USDT**, gross {s['gross_pnl']:.2f}USDT; "
        f"chi phí {s['cost_usdt']:.2f}USDT. Mean net R **{s['mean_net_r']:.4f}**; PF USDT {s['profit_factor']:.3f}.", '',
        f"Phí {s['exchange_fee']:.2f}, slippage {s['slippage_cost']:.2f}, funding paid {s['funding_paid']:.2f}USDT. "
        'Funding paid âm là khoản nhận; không lấy trị tuyệt đối khi cộng chi phí.', '',
        f"Chi phí của177 lệnh thắng: {s['winners_cost_usdt']:.2f}USDT; của242 lệnh thua: {s['losers_cost_usdt']:.2f}USDT. "
        f"Có {s['gross_win_to_net_loss']} lệnh gross dương chuyển sang net âm sau chi phí. "
        f"Lãi thắng trung bình {s['mean_win']:.2f}; lỗ thua trung bình {s['mean_loss']:.2f}USDT.", '',
        '## Chuẩn hóa rủi ro và giới hạn diễn giải', '',
        '1R = quantity × |entry_price − stop ban đầu trong entry_features|. Stop đúng hướng và cách giá vào3ATR. '
        'Net R = (gross − phí − slippage − funding paid)/1R. Mẫu số là rủi ro giá danh nghĩa ban đầu, không gồm chi phí; '
        'gross R, net R và cost R có trong CSV. Mean R cho mỗi lệnh trọng số bằng nhau; USDT phản ánh cả kích thước lệnh. '
        'Tổng R không phải return tài khoản hoặc max DD.', '',
        'Ghép bằng thời điểm UTC vào lệnh + market + symbol. Kiểm tra419 khóa duy nhất, không entry thừa/thiếu, '
        'signal_available_at ≤ opened_at, stop/risk dương và đối soát PnL/chi phí từng lệnh. '
        'Dùng đặc trưng H4 đã đóng được nhật ký ghi tại lúc vào, không tái tính bằng dữ liệu tương lai.', '',
        '## Phụ thuộc lệnh thắng lớn', '',
        'Chỉ xếp các lệnh net dương. Xếp riêng theo net USDT và net R; giữ ID để truy lại. '
        'Cột phần còn lại là phép trừ mô tả trên nhật ký cố định. Loại lệnh khi giao dịch sẽ thay đổi vốn, sizing, '
        'tín hiệu và guard; bảng này không phải backtest bỏ các lệnh đó.', '',
        '| Xếp theo | Top | Net USDT chọn | Net R chọn | Net USDT còn lại | Mean net R còn lại |',
        '|---|---:|---:|---:|---:|---:|']
    for basis, tails in result['tails'].items():
        for t in tails:
            lines.append(f"| {basis} | {t['k']} | {t['selected']['net_pnl']:.3f} | "
                f"{t['selected']['net_r']:.3f} | {t['remaining']['net_pnl']:.3f} | "
                f"{format_number(t['remaining']['mean_net_r'])} |")
    lines += ['', '![Phân bố net R](net-r-distribution.png)', '',
              '![Đóng góp lệnh thắng](winner-contribution.png)', '',
              '## Sáu phần coin × market', '']
    lines += group_table([g for g in result['groups'] if g['feature'] == 'all'])
    lines += ['', '## Tiêu chí nhóm cần xem lại', '',
        'Khóa trước kết quả: n≥20 và mean net R<0 toàn kỳ; âm trong ít nhất3 năm đầy đủ2022–2025, '
        'mỗi năm đủ≥5 lệnh. Năm2026 chưa đủ kỳ không được tính vào tiêu chí3 năm. '
        'Nhóm dưới20 lệnh được gắn cờ mẫu ít; không hạ ngưỡng hoặc chia lại bins sau kết quả.', '',
        f"Có **{len(result['candidates'])} nhóm** đạt tiêu chí." if result['candidates'] else
        '**Không có nhóm nào đạt tiêu chí đã chốt.** Không tự chọn bộ lọc hoặc nới tiêu chí.', '']
    chosen = [next(g for g in result['groups'] if g['group_id'] == key) for key in result['candidates']]
    if chosen:
        lines += group_table(chosen)
        lines += ['', '| Nhóm | 2022 n/mean R | 2023 n/mean R | 2024 n/mean R | 2025 n/mean R | 2026 n/mean R (chưa đủ) |',
                  '|---|---:|---:|---:|---:|---:|']
        for g in chosen:
            values = [f"{g['years'][str(y)]['count']}/{format_number(g['years'][str(y)]['mean_net_r'])}"
                      for y in range(2022, 2027)]
            lines.append('| '+g['group_id']+' | '+' | '.join(values)+' |')
        lines += ['', 'Các nhóm có thể chứa cùng lệnh và cùng lệnh thắng lớn; không cộng PnL nhóm với nhau. '
                  'Đạt tiêu chí chỉ là giả thuyết cần xem lại, chưa chứng minh loại nhóm sẽ cải thiện chiến lược.']
    lines += ['', '## Các khoảng đã khóa', '',
        'Mỗi lần chỉ xét một biến; không quét tổ hợp các biến. Với mỗi biến, phân nhóm toàn portfolio và riêng6 phần coin/market.', '',
        '| Biến | Công thức/đơn vị | Khoảng |', '|---|---|---|',
        '| ADX | ADX H4 | (20,25]; (25,35]; >35 |',
        '| ADX tăng | ADX − prior ADX, điểm | (0,1]; (1,3]; >3 |',
        '| ATR% | ATR / close H4 tín hiệu ×100 | (0,2]; (2,4]; >4 |',
        '| Volume | volume / VolumeMA20 | (1.2,2]; (2,3]; >3 |',
        '| Khoảng EMA50 | side × (close H4 − EMA50)/ATR; side +1 Spot, −1 Short | (0,1]; (1,2]; >2 |', '',
        'Bins dùng mô tả, không phải ngưỡng giao dịch. Các nhóm khác biến hoặc portfolio/coin chồng lấp. '
        'Overlap trong mọi bảng/CSV dùng top10 net USDT toàn portfolio, cùng một danh sách cố định.', '',
        '## Theo thời điểm mở lệnh', '',
        'Toàn bộ net của lệnh đóng được gán vào năm/nửa năm UTC mở lệnh, kể cả lệnh đóng ở kỳ kế tiếp. '
        'Đây không phải thay đổi marked equity trong kỳ. Năm2026 và2026-H2 chưa đủ;2026-H1 là nửa năm đầy đủ.', '',
        '| Kỳ vào | Đủ kỳ | Lệnh | Thắng/thua | Net USDT | Mean net R | PF | Chi phí USDT |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    portfolio = result['groups'][0]
    for cohorts in (portfolio['years'], portfolio['halves']):
        for p, v in cohorts.items():
            lines.append(f"| {p} | {v['complete_period']} | {v['count']} | {v['wins']}/{v['losses']} | "
                f"{v['net_pnl']:.3f} | {format_number(v['mean_net_r'])} | "
                f"{format_number(v['profit_factor'])} | {v['cost_usdt']:.3f} |")
    lines += ['', '## Phân nhóm toàn portfolio', '']
    lines += group_table([g for g in result['groups'] if g['scope'] == 'portfolio' and g['feature'] != 'all'])
    lines += ['', '## Bàn giao và giới hạn', '',
        'Đủ112 nhóm, mỗi nhóm có toàn kỳ +5 năm +10 nửa năm trong group-stats.csv/analysis.json. '
        'trades-enriched.csv giữ419 lệnh với stop ban đầu, đặc trưng, bins, USDT, R và flags top winners. '
        'Các biểu đồ là ảnh tĩnh xuất bằng matplotlib; số bins histogram chỉ phục vụ hiển thị.', '',
        'Dữ liệu lịch sử đã dùng nhiều lần, không phải OOS mới. Các ngoại lệ nguồn đã duyệt của bộ2 được giữ nguyên. '
        'Phân nhóm nhiều lần trên cùng mẫu có nguy cơ khớp lịch sử; số lệnh trong một năm không bảo đảm độc lập. '
        'Chưa thay đổi điều kiện vào/thoát, loại coin, risk M15 hoặc lệnh limit; chưa chọn bộ lọc để giao dịch. '
        'Nếu thử giả thuyết sau này cần kế hoạch khóa riêng, replay sizing/chi phí và validation riêng.', '',
        f"Analysis commit: `{binding['analysis_commit']}`; source engine commit: `{binding['source_engine_commit']}`.",
        f"Config: `{binding['config_checksum']}`; dataset: `{binding['dataset_checksum']}`.",
        'binding.json/grouping-config.json được ghi trước khi tính kết quả; verification.json xác nhận đối soát, '
        'lặp phân tích cơ học và nguồn18 báo cáo không đổi. manifest.json khóa checksum các file bàn giao.', '']
    return '\n'.join(lines)


def charts(rows, result, root):
    import os
    os.environ.setdefault('MPLCONFIGDIR', '/tmp/donchian-setup2-matplotlib')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5), layout='constrained')
    values = [float(r['net_r']) for r in rows]
    ax.hist(values, bins=50, color='#39798b', edgecolor='white')
    ax.axvline(0, color='#ad3b3b', linewidth=1)
    ax.axvline(float(result['overall']['mean_net_r']), color='#d28a19', linestyle='--', label='Mean net R')
    ax.set(xlabel='Net R per closed trade (initial nominal price risk)', ylabel='Trades',
           title='Setup 2: 419 existing trades, 2022-01-01 to 2026-10-02 UTC')
    ax.legend()
    fig.savefig(root/'net-r-distribution.png', dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(2, 1, figsize=(10, 8), layout='constrained')
    for ax, basis, field, unit in zip(axes, ('net_pnl', 'net_r'), ('net_pnl', 'net_r'), ('USDT', 'R')):
        winners = ranked_winners(rows, basis)[:20]
        cumulative = [float(sum((r[field] for r in winners[:k]), ZERO)) for k in range(1, len(winners)+1)]
        ax.plot(range(1, len(winners)+1), cumulative, marker='o', color='#39798b', label='Selected winners')
        ax.axhline(float(result['overall'][field]), color='#ad3b3b', linestyle='--', label='All-trade net sum')
        ax.set(xlabel=f'Largest positive winners ranked by {basis}', ylabel=f'Cumulative net {unit}',
               xticks=[1, 5, 10, 15, 20])
        ax.grid(alpha=.2)
        ax.legend()
    fig.suptitle('Existing-journal sums only; excluding trades is not a replay')
    fig.savefig(root/'winner-contribution.png', dpi=160)
    plt.close(fig)
    for name in ('net-r-distribution.png', 'winner-contribution.png'):
        (root/name).chmod(0o600)


def run(comparison_path, output_root, inventory_path):
    from pathlib import Path
    from intraday.replay_v2.artifacts import _write
    from intraday.replay_v2.donchian_oos_analysis import journal
    from intraday.replay_v2.metrics import encoded, fingerprint
    from intraday.replay_v2.portfolio_study import file_hash
    root = Path(output_root).resolve()
    binding, item = freeze(comparison_path, root, inventory_path)
    print('Frozen binding and grouping policy; reading existing journals.', flush=True)
    trades, events = journal(item, 'trades'), journal(item, 'events')
    rows = join_trades(trades, events)
    result = analyze(rows)
    reconcile_baseline(result, item)
    repeated_rows = join_trades(list(reversed(trades)), list(reversed(events)))
    repeated = analyze(repeated_rows)
    if fingerprint(rows) != fingerprint(repeated_rows) or fingerprint(result) != fingerprint(repeated):
        raise ValueError('diagnostic mechanical repeat differs')
    flags = {}
    for basis in POLICY['tail_bases']:
        ordered = ranked_winners(rows, basis)
        flags[basis] = {r['trade_id']: i+1 for i, r in enumerate(ordered)}
    enriched = []
    for row in rows:
        extra = {}
        for basis in POLICY['tail_bases']:
            rank = flags[basis].get(row['trade_id'])
            extra['positive_'+basis+'_rank'] = rank
            extra.update({f'top{k}_{basis}': rank is not None and rank <= k for k in POLICY['tails']})
        enriched.append(dict(row, **extra))
    publication = dict(result, research_only=True, activation_allowed=False,
        analysis_commit=binding['analysis_commit'], config_checksum=binding['config_checksum'],
        dataset_checksum=binding['dataset_checksum'], policy_checksum=binding['policy_checksum'],
        initial_capital=1000, final_equity=item['summary']['final_equity_known'],
        max_dd_pct=item['summary']['max_drawdown_known_pct'], daily_stops=item['summary']['daily_stops'],
        attribution=POLICY['period_attribution'], descriptive_only=True)
    _write(root/'analysis.json', encoded(publication))
    _write(root/'trades-enriched.csv', csv_text(enriched))
    _write(root/'group-stats.csv', csv_text(export_groups(result)))
    _write(root/'report.md', markdown(result, binding))
    _write(root/'analysis-source.py', Path(__file__).read_text())
    charts(rows, result, root)
    print('Outputs generated; verifying all frozen sources and 18 original reports.', flush=True)
    for key in ('source_files_sha256', 'source_snapshot_sha256', 'analysis_files_sha256',
                'current_engine_files_sha256'):
        verify_snapshot(binding[key])
    verification = dict(status='passed', exact_join_count=len(rows), causal_feature_checks=True,
        baseline_counts=[419, 177, 242, 0], baseline_net_pnl_reconciled=True,
        baseline_costs_reconciled=True, original_coin_market_contributions_reconciled=True,
        positive_initial_risk=True, gross_net_cost_identities=True, locked_bins_reconciled=True,
        cohort_count_and_pnl_reconciled=True, original_18_reports_unchanged=True,
        deterministic_analysis_repeat=True, rows_checksum=fingerprint(rows),
        analysis_checksum=fingerprint(result), groups=len(result['groups']),
        group_csv_rows=len(export_groups(result)), strategy_replays=0,
        finished_at=datetime.now(timezone.utc).isoformat())
    _write(root/'verification.json', encoded(verification))
    files = {p.name: dict(bytes=p.stat().st_size, sha256=file_hash(p)) for p in sorted(root.iterdir())}
    _write(root/'manifest.json', encoded(dict(binding_checksum=fingerprint(binding), files=files)))
    return publication


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison', required=True)
    parser.add_argument('--report-root', required=True)
    parser.add_argument('--inventory', required=True, help='verified original 18-case all-setups.json')
    args = parser.parse_args()
    outcome = run(args.comparison, args.report_root, args.inventory)
    print(f"Complete: {outcome['overall']['count']} trades; {len(outcome['candidates'])} review groups; "
          f"net {outcome['overall']['net_pnl']:.6f} USDT; output {args.report_root}")
