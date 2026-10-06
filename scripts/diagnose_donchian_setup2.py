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
    candidates = sorted((g['group_id'] for g in groups if g['needs_review']),
        key=lambda key: next((g['overall']['mean_net_r'], g['overall']['net_pnl'], key)
                             for g in groups if g['group_id'] == key))
    return dict(overall=overall, tails=tails, groups=groups, candidates=candidates)
