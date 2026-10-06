"""Independent Decimal audit and readable rendering of published diagnostics."""

import csv
from datetime import datetime, timezone
from decimal import Decimal as D, localcontext
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def same(actual, expected):
    assert abs(D(str(actual))-D(str(expected))) < D('1e-18'), (actual, expected)


def readable_report(text, groups):
    # Literal group IDs contain pipes, which must be escaped in Markdown tables.
    for group in sorted(groups, key=lambda g: len(g['group_id']), reverse=True):
        key = group['group_id']
        text = text.replace(key, key.replace('|', r'\|'))
    return text.replace('](net-r-distribution.png)', '](../net-r-distribution.png)').replace(
        '](winner-contribution.png)', '](../winner-contribution.png)')


def audit(root):
    root = Path(root).resolve()
    publication = json.loads((root/'manifest.json').read_text())
    for name, info in publication['files'].items():
        assert digest(root/name) == info['sha256'], name
        assert (root/name).stat().st_size == info['bytes'], name
    binding = json.loads((root/'binding.json').read_text())
    policy = json.loads((root/'grouping-config.json').read_text())
    result = json.loads((root/'analysis.json').read_text())
    verification = json.loads((root/'verification.json').read_text())
    assert verification['status'] == 'passed' and verification['original_18_reports_unchanged']
    script_sha = next(v for k, v in binding['analysis_files_sha256'].items()
                      if k.endswith('/diagnose_donchian_setup2.py'))
    assert digest(root/'analysis-source.py') == script_sha
    expected_bins = {'adx': ['20', '25', '35'], 'adx_rise': ['0', '1', '3'],
        'atr_pct': ['0', '2', '4'], 'volume_ratio': ['1.2', '2', '3'],
        'ema50_distance_atr': ['0', '1', '2']}
    assert policy['bins'] == expected_bins
    assert (policy['minimum_group'], policy['minimum_year'], policy['minimum_negative_years']) == (20, 5, 3)
    assert policy['complete_years'] == [2022, 2023, 2024, 2025]
    receipt = json.loads(Path(binding['source_comparison']).read_text())
    item = receipt['results'][1]
    assert item['variant'] == 'Setup-2'
    source = Path(item['report_directory'])
    for name in ('trades.jsonl', 'events.jsonl'):
        assert digest(source/name) == binding['source_snapshot_sha256'][str(source/name)]
    trades = [json.loads(s) for s in (source/'trades.jsonl').read_text().splitlines()]
    events = [json.loads(s) for s in (source/'events.jsonl').read_text().splitlines()]
    original = {(t['opened_at'], t['market'], t['symbol']): t for t in trades}
    entries = {(e['at'], e['market'], e['symbol']): e for e in events if e['kind'] == 'entry_features'}
    with (root/'trades-enriched.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(original) == len(entries) == 419
    assert len({r['trade_id'] for r in rows}) == 419
    with localcontext() as ctx:
        ctx.prec = 50
        for row in rows:
            key = (row['opened_at'], row['market'], row['symbol'])
            t, e = original[key], entries[key]
            f = e['features']
            for field in ('net_pnl', 'gross_pnl', 'exchange_fee', 'slippage_cost', 'funding_paid',
                          'entry_price', 'exit_price', 'quantity'):
                same(row[field], t[field])
            risk = D(t['quantity'])*abs(D(t['entry_price'])-D(e['stop']))
            assert risk > 0
            same(row['initial_risk_usdt'], risk)
            same(row['net_r'], D(t['net_pnl'])/risk)
            cost = D(t['exchange_fee'])+D(t['slippage_cost'])+D(t['funding_paid'])
            same(row['cost_usdt'], cost)
            same(row['cost_r'], cost/risk)
            same(row['gross_r'], D(t['gross_pnl'])/risk)
            dt = datetime.fromisoformat(row['opened_at']).astimezone(timezone.utc)
            assert datetime.fromisoformat(e['signal_available_at']) <= dt
            assert int(row['entry_year']) == dt.year
            assert row['entry_half'] == f'{dt.year}-H{1 if dt.month <= 6 else 2}'
            sign = D(1 if t['market'] == 'spot' else -1)
            features = dict(adx=D(f['adx']), adx_rise=D(f['adx'])-D(f['prior_adx']),
                atr_pct=D(f['atr'])/D(f['close'])*100,
                volume_ratio=D(f['volume'])/D(f['volume_ma']),
                ema50_distance_atr=sign*(D(f['close'])-D(f['ema50']))/D(f['atr']))
            for name, value in features.items():
                same(row[name], value)
                lo, mid, hi = map(D, expected_bins[name])
                assert value > lo
                label = f'({lo},{mid}]' if value <= mid else f'({mid},{hi}]' if value <= hi else f'>{hi}'
                assert row[name+'_band'] == label
        for basis, tails in result['tails'].items():
            ordered = sorted((r for r in rows if D(r['net_pnl']) > 0),
                             key=lambda r: (-D(r[basis]), r['trade_id']))
            for k in (1, 5, 10, 20):
                ids = [r['trade_id'] for r in ordered[:k]]
                record = next(t for t in tails if t['k'] == k)
                assert ids == record['trade_ids']
                for field in ('net_pnl', 'net_r'):
                    same(record['selected'][field], sum((D(r[field]) for r in ordered[:k]), D(0)))
                    same(D(record['selected'][field])+D(record['remaining'][field]), result['overall'][field])
                for row in rows:
                    assert row[f'top{k}_{basis}'] == str(row['trade_id'] in ids)
        top_ids = set(result['tails']['net_pnl'][2]['trade_ids'])
        candidates = set()
        for g in result['groups']:
            subset = [r for r in rows if (g['scope'] == 'portfolio' or r['pair'] == g['scope']) and
                      (g['feature'] == 'all' or r[g['feature']+'_band'] == g['band'])]
            assert [r['trade_id'] for r in subset] == g['trade_ids']
            assert len(subset) == g['overall']['count']
            assert g['overall']['wins'] == sum(D(r['net_pnl']) > 0 for r in subset)
            assert g['overall']['losses'] == sum(D(r['net_pnl']) < 0 for r in subset)
            assert g['overall']['low_sample'] == (len(subset) < 20)
            for field in ('net_pnl', 'net_r', 'cost_usdt'):
                same(g['overall'][field], sum((D(r[field]) for r in subset), D(0)))
            if subset:
                same(g['overall']['mean_net_r'], sum((D(r['net_r']) for r in subset), D(0))/len(subset))
            overlap = [r['trade_id'] for r in subset if r['trade_id'] in top_ids]
            assert g['overall']['global_top10_ids'] == overlap
            negative_years = []
            for kind, key in (('years', 'entry_year'), ('halves', 'entry_half')):
                for period, stats in g[kind].items():
                    assert stats['complete_period'] == (period != '2026' if kind == 'years' else period != '2026-H2')
                    cohort = [r for r in subset if r[key] == period]
                    assert len(cohort) == stats['count']
                    for field in ('net_pnl', 'net_r', 'cost_usdt'):
                        same(stats[field], sum((D(r[field]) for r in cohort), D(0)))
                    if cohort:
                        mean = sum((D(r['net_r']) for r in cohort), D(0))/len(cohort)
                        same(stats['mean_net_r'], mean)
                        if kind == 'years' and int(period) <= 2025 and len(cohort) >= 5 and mean < 0:
                            negative_years.append(int(period))
            eligible = len(subset) >= 20 and D(g['overall']['mean_net_r']) < 0 and len(negative_years) >= 3
            assert g['needs_review'] == eligible
            assert g['qualifying_negative_years'] == negative_years
            if eligible:
                candidates.add(g['group_id'])
        assert candidates == set(result['candidates'])
        assert abs(D(result['overall']['net_pnl'])-D('663.9825162550667')) < D('1e-7')
        same(result['overall']['cost_usdt'], '174.5137095989663670512010953')
    with (root/'group-stats.csv').open() as stream:
        csv_groups = list(csv.DictReader(stream))
    assert len(csv_groups) == 1792
    indexed = {(r['group_id'], r['period_type'], r['period']): r for r in csv_groups}
    assert len(indexed) == 1792
    for g in result['groups']:
        records = [('overall', 'all', g['overall'])]
        records += [(kind, period, stats) for kind in ('years', 'halves') for period, stats in g[kind].items()]
        for kind, period, stats in records:
            exported = indexed[(g['group_id'], kind, period)]
            for field, value in stats.items():
                if isinstance(value, list):
                    assert json.loads(exported[field]) == value
                else:
                    assert exported[field] == ('' if value is None else str(value))
    audit_root = root/'audit'
    audit_root.mkdir(mode=0o700, exist_ok=False)
    from intraday.replay_v2.artifacts import _write
    from intraday.replay_v2.metrics import encoded
    repo = Path(__file__).resolve().parents[1]
    proof = dict(status='passed', independent_decimal_precision=50, trades=419, groups=112,
        group_csv_rows=1792, manifest_verified=True, raw_journal_join_reconciled=True,
        per_trade_r_and_features_reconciled=True, tails_and_flags_reconciled=True,
        group_memberships_and_cohorts_reconciled=True, locked_candidate_criterion_reconciled=True,
        publication_manifest_sha256=digest(root/'manifest.json'), strategy_replays=0,
        audit_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip())
    _write(audit_root/'verification.json', encoded(proof))
    _write(audit_root/'audit-source.py', Path(__file__).read_text())
    _write(audit_root/'report.md', readable_report((root/'report.md').read_text(), result['groups']))
    _write(audit_root/'manifest.json', encoded(dict(parent_manifest_sha256=digest(root/'manifest.json'),
        files={p.name: dict(sha256=digest(p), bytes=p.stat().st_size) for p in sorted(audit_root.iterdir())})))
    return proof


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report_root')
    args = parser.parse_args()
    print(json.dumps(audit(args.report_root), indent=2))
