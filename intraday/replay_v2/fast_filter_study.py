"""Setup-2 fast filter research (docs/setup2-fast-filter-research.md), pre-registered 2026-10-19.

Replays the ADR-007 slot portfolio (3 positions, one per coin, ADX ranking, rotation margin 5, PnL gate) on the
live evaluator's observations, once per entry variant and entry mode, and scores the registered criteria
against the current rule B:
- V1 enters every unblocked Donchian breakout, at half size when a filter fails;
- V2 enters such a breakout at half size and adds the other half once EMA200, EMA50, ADX and DMI agree;
- V3 swaps the EMA50/EMA200 filters for EMA20/EMA50, V4 the ADX rule for ADX above its value 3 bars back,
  V5 the EMA50 and ADX/DMI filters for a breakout body of at least one ATR.
Mode 'market' fills at the next H4 open; mode 'limit' fills a Perp entry at a limit 0.25 ATR above the signal
close during the next H4 bar, read on M15 (Spot stays at market).

Prices are floats: the study compares outcomes and never sizes an order. Nothing here touches the runtime.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import fmean

from intraday.replay_v2 import donchian_rotation_select as sel

CAP, SLEEVE, SLOTS, MARGIN = 0.05, 0.5, 3, 5
COST_PCT = {'perp': 0.20, 'spot': 0.30}   # round trip: the replay profile's fee + slippage on both sides
LIMIT_ATR, QUARTERS = 0.25, 16
HALF, TIER2_BARS = 0.5, 6
TIER2_FILTERS = frozenset(('ema200', 'ema50', 'adx', 'dmi'))
VARIANTS = ('B', 'V1', 'V2', 'V3', 'V4', 'V5')
MODES = ('market', 'limit')
MAX_DD = 10.0
SIDES = {'perp': -1, 'spot': 1}
NUMBERS = ('mult', 'atr', 'adx', 'prior_adx', 'adx3', 'plus_di', 'minus_di', 'volume', 'volume_ma', 'ema20',
           'prior_ema20', 'ema50')


@dataclass(frozen=True)
class Route:
    symbol: str
    market: str
    side: int
    bars: tuple      # (opened_at, open, high, low, close)
    obs: tuple       # per bar: the observation dict, None before warm-up
    index: dict      # opened_at -> bar index


def observation(item):
    if item is None:
        return None
    s = {k: None if item.get(k) is None else float(item[k]) for k in NUMBERS}
    return dict(s, donchian=item['donchian'], failed=frozenset(item['failed']), blockers=tuple(item['blockers']),
                exit=item['exit'])


def load(raw):
    """Routes and the Perp M15 windows (16 quarters per H4 bar after each breakout) of an export."""
    routes = {}
    for key, rows in raw['series'].items():
        symbol, market = key.split(':')
        bars = tuple((r[0], *map(float, r[1:5])) for r in rows)
        routes[key] = Route(symbol, market, SIDES[market], bars, tuple(observation(r[5]) for r in rows),
                            {b[0]: i for i, b in enumerate(bars)})
    m15 = {k: [None if b is None else (b[0], *map(float, b[1:5])) for b in v] for k, v in raw['m15'].items()}
    return routes, m15


# Entry rules.

def fast_ema_fails(side, s, close):
    """V3: close on the side of EMA50, and on the side of a sloping EMA20."""
    if s['ema50'] is None or s['ema20'] is None or s['prior_ema20'] is None:
        return True
    return side*(close-s['ema50']) <= 0 or side*(close-s['ema20']) <= 0 or side*(s['ema20']-s['prior_ema20']) <= 0


def entry_factor(variant, side, s, bar):
    """Size factor of an entry on closed bar `bar` with observation `s`: 1, HALF or 0 (no entry)."""
    if s is None or not s['donchian'] or s['blockers']:
        return 0.0
    _, open_, _, _, close = bar
    failed = set(s['failed'])
    if variant in ('B', 'V1', 'V2'):
        return 1.0 if not failed else (0.0 if variant == 'B' else HALF)
    if variant == 'V3':
        failed -= {'ema200', 'ema50'}
        if fast_ema_fails(side, s, close):
            failed.add('ema20')
    elif variant == 'V4':
        failed.discard('adx')
        if s['adx'] is None or s['adx3'] is None or s['adx'] <= s['adx3']:
            failed.add('adx3')
    elif variant == 'V5':
        failed -= {'ema50', 'adx', 'dmi'}
        if s['atr'] is None or side*(close-open_) < s['atr']:
            failed.add('body')
    else:
        raise ValueError(f'unknown variant {variant}')
    return 0.0 if failed else 1.0


def tier2_ready(s):
    """V2 adds its second half after a closed bar on which EMA200, EMA50, ADX and DMI all pass."""
    return s is not None and not (s['failed'] & TIER2_FILTERS)


class Trail:
    """ATR14 x 3 trail from the first entry, capped at 5% from it; never loosens."""

    def __init__(self, side, entry, atr):
        self.side, self.extreme = side, entry
        stop, hard = entry-side*3*atr, entry*(1-side*CAP)
        self.stop = max(stop, hard) if side > 0 else min(stop, hard)

    def update(self, high, low, atr):
        self.extreme = max(self.extreme, high) if self.side > 0 else min(self.extreme, low)
        c = self.extreme-self.side*3*atr
        self.stop = max(self.stop, c) if self.side > 0 else min(self.stop, c)

    def hit(self, high, low):
        return low <= self.stop if self.side > 0 else high >= self.stop


# Portfolio replay.

def run(routes, m15, variant, *, mode='market', cost_mult=1.0):
    """Closed tranches (one per entry fill) and the blocked-entry counts of one variant and entry mode."""
    trades, pos, pending, blocked = [], {}, {}, defaultdict(int)
    times = sorted({b[0] for r in routes.values() for b in r.bars})

    def open_(rt, price, weight, tier, signal, signal_index, factor, atr, add):
        if rt in pos:
            pos[rt]['tranches'].append(dict(entry=price, weight=weight, tier=tier))
            return
        pos[rt] = dict(trail=Trail(routes[rt].side, price, atr), signal=signal, signal_index=signal_index,
                       factor=factor, add_until=signal_index+TIER2_BARS if add else None,
                       base_weight=weight, tranches=[dict(entry=price, weight=weight, tier=tier)])

    def close(rt, at, price, reason):
        p, r = pos.pop(rt), routes[rt]
        for tr in p['tranches']:
            ret = r.side*(price/tr['entry']-1)*100-COST_PCT[r.market]*cost_mult
            trades.append(dict(route=rt, market=r.market, signal=p['signal'], exited=at, ret=ret,
                               pnl=ret*tr['weight'], reason=reason, tier=tr['tier'], factor=p['factor']))

    def book(rt, i):
        p, r = pos[rt], routes[rt]
        price = r.bars[i][1] if i is not None else p['tranches'][0]['entry']
        weight = sum(t['weight'] for t in p['tranches'])
        pnl = sum(r.side*(price/t['entry']-1)*t['weight'] for t in p['tranches'])
        return pnl, pnl/weight

    for t in times:
        live = {rt: r.index[t] for rt, r in routes.items() if t in r.index}
        # 1. Exits at the open: gaps through the stop, then Donchian exits of the last closed bar.
        for rt in list(pos):
            if rt not in live:
                continue
            r, i = routes[rt], live[rt]
            o = r.bars[i][1]
            if (o <= pos[rt]['trail'].stop) if r.side > 0 else (o >= pos[rt]['trail'].stop):
                close(rt, t, o, 'gap')
            elif i and r.obs[i-1] and r.obs[i-1]['exit']:
                close(rt, t, o, 'donchian_exit')
        # 2. V2 second half at the open after a confirming bar inside the window.
        for rt in list(pos):
            p = pos[rt]
            if p['add_until'] is None or rt not in live:
                continue
            r, i = routes[rt], live[rt]
            if p['signal_index'] < i-1 <= p['add_until'] and tier2_ready(r.obs[i-1]):
                open_(rt, r.bars[i][1], p['base_weight'], 2, p['signal'], p['signal_index'], p['factor'], None, False)
                p['add_until'] = None
            elif i-1 >= p['add_until']:
                p['add_until'] = None
        # 3. Entries of the last closed bar, ranked and granted by the runtime's selection.
        busy = {routes[rt].symbol for rt in list(pos)+list(pending)}
        cands, factors = [], {}
        for rt, i in live.items():
            r = routes[rt]
            if not i or rt in pos or rt in pending or r.symbol in busy:
                continue
            s = r.obs[i-1]
            factor = entry_factor(variant, r.side, s, r.bars[i-1])
            if factor:
                factors[rt] = factor
                ratio = s['volume']/s['volume_ma'] if s['volume_ma'] else 0
                cands.append(sel.Candidate(r.market, r.symbol, Decimal(str(s['mult']*factor)),
                                           Decimal(str(s['adx'] or 0)), Decimal(str(ratio))))
        granted = []
        if cands:
            held, total = [], 0.0
            for rt in pos:
                r, i = routes[rt], live.get(rt)
                f = r.obs[i-1] if i else None
                strength = sel.trend_strength({k: None if f is None or f[k] is None else Decimal(str(f[k]))
                                               for k in ('adx', 'plus_di', 'minus_di')}, r.side)
                pnl, ratio = book(rt, i)
                total += pnl
                held.append(sel.Held(r.market, r.symbol, strength, Decimal(str(ratio)), i is not None))
            for rt in pending:
                held.append(sel.Held(routes[rt].market, routes[rt].symbol, Decimal(999), Decimal(0), False))
            if sel.pnl_gate_blocks(len(pos), Decimal(str(total))):
                blocked['pnl_gate'] += len(cands)
            else:
                for st in sel.select_entries(cands, held, max_positions=SLOTS, limit_scope='total', rotation=True,
                                             margin=Decimal(MARGIN)):
                    if st.kind == 'blocked':
                        blocked[st.reason] += 1
                    else:
                        granted.append((f'{st.symbol}:{st.market}', st.victim))
        filled_now = set()
        for rt, victim in granted:
            if victim:
                vr = f'{victim[1]}:{victim[0]}'
                close(vr, t, routes[vr].bars[live[vr]][1], 'rotated')
            r, i = routes[rt], live[rt]
            s, factor = r.obs[i-1], factors[rt]
            weight, signal = SLEEVE/SLOTS*s['mult']*factor, r.bars[i-1][0]
            add = variant == 'V2' and factor < 1
            if r.market == 'perp' and mode == 'limit':
                pending[rt] = dict(level=r.bars[i-1][4]+LIMIT_ATR*s['atr'], atr=s['atr'], weight=weight,
                                   signal=signal, signal_index=i-1, factor=factor, add=add)
            else:
                open_(rt, r.bars[i][1], weight, 1, signal, i-1, factor, s['atr'], add)
        # 4. Pending limits fill on M15 inside this H4 bar; the stop is read from the fill's M15 bar on.
        for rt in list(pending):
            if rt not in live:
                continue
            p, r, i = pending.pop(rt), routes[rt], live[rt]
            quarter = (m15.get(f"{r.symbol}:{p['signal']}") or [])[:QUARTERS]
            if len(quarter) < QUARTERS or any(q is None for q in quarter):
                quarter = [(t, *r.bars[i][1:5])]   # no M15: the H4 bar stands in
            for k, (qt, qo, qh, ql, qc) in enumerate(quarter):
                fill = qo if qo >= p['level'] else p['level'] if qh >= p['level'] else None
                if fill is None:
                    continue
                open_(rt, fill, p['weight'], 1, p['signal'], p['signal_index'], p['factor'], p['atr'], p['add'])
                filled_now.add(rt)
                rest = quarter[k:]
                if any(q[2] >= pos[rt]['trail'].stop for q in rest):
                    close(rt, qt, pos[rt]['trail'].stop, 'stop')
                else:
                    pos[rt]['after_fill'] = (max(q[2] for q in rest), min(q[3] for q in rest))
                break
        # 5. Intrabar stops of positions held through the bar, then the trail ratchets at the close.
        for rt in list(pos):
            if rt not in live:
                continue
            r, i = routes[rt], live[rt]
            _, o, h, l, c = r.bars[i]
            if rt not in filled_now and pos[rt]['trail'].hit(h, l):
                close(rt, t, pos[rt]['trail'].stop, 'stop')
                continue
            cur = r.obs[i]
            hh, ll = pos[rt].pop('after_fill', (h, l))
            if cur and not cur['exit'] and cur['atr']:
                pos[rt]['trail'].update(hh, ll, cur['atr'])
    for rt in list(pos):
        last = routes[rt].bars[-1]
        close(rt, last[0], last[4], 'open')
    return trades, dict(blocked)


# Scoring.

def drawdown(trades):
    """Largest fall of the equity curve (simple sum, % of capital) ordered by exit time."""
    eq = peak = dd = 0.0
    for t in sorted(trades, key=lambda t: t['exited']):
        eq += t['pnl']
        peak = max(peak, eq)
        dd = max(dd, peak-eq)
    return dd


def midpoint(routes):
    """Middle of the replay span: from the first warmed-up bar to the last bar of any route."""
    first = min(r.bars[next(i for i, o in enumerate(r.obs) if o is not None)][0] for r in routes.values())
    last = max(r.bars[-1][0] for r in routes.values())
    a, b = datetime.fromisoformat(first), datetime.fromisoformat(last)
    return (a+(b-a)/2).isoformat()


def summary(trades, blocked, mid, baseline_signals=None):
    total = sum(t['pnl'] for t in trades)
    dd = drawdown(trades)
    first = [t for t in trades if t['tier'] == 1]
    result = dict(
        total=total, drawdown=dd, ratio=total/dd if dd else None, positions=len(first),
        mean_ret=fmean(t['ret'] for t in first) if first else None,
        win_rate=sum(t['ret'] > 0 for t in first)/len(first) if first else None,
        perp=sum(t['pnl'] for t in trades if t['market'] == 'perp'),
        spot=sum(t['pnl'] for t in trades if t['market'] == 'spot'),
        years={y: sum(t['pnl'] for t in trades if t['signal'].startswith(y)) for y in ('2024', '2025', '2026')},
        halves=[sum(t['pnl'] for t in trades if t['signal'] < mid), sum(t['pnl'] for t in trades if t['signal'] >= mid)],
        half_size=sum(t['factor'] < 1 for t in first), blocked=blocked)
    tier2 = [t for t in trades if t['tier'] == 2]
    if tier2:
        result['tier2'] = dict(n=len(tier2), pnl=sum(t['pnl'] for t in tier2), mean_ret=fmean(t['ret'] for t in tier2),
                               tier1_of_added=sum(t['pnl'] for t in first if any(
                                   u['route'] == t['route'] and u['signal'] == t['signal'] for u in tier2)))
    if baseline_signals is not None:
        extra = [t for t in first if (t['route'], t['signal']) not in baseline_signals]
        result['extra'] = dict(n=len(extra), pnl=sum(t['pnl'] for t in extra),
                               mean_ret=fmean(t['ret'] for t in extra) if extra else None,
                               win_rate=sum(t['ret'] > 0 for t in extra)/len(extra) if extra else None)
    return result


def criteria(variant, base):
    """The five registered criteria of a variant against B; `variant`/`base` map mode (and 'double') to summaries."""
    better = lambda a, b: a is not None and b is not None and a > b
    return {
        '1_total_beats_both': all(variant[m]['total'] > base[m]['total'] for m in MODES),
        '2_drawdown_at_most_10': all(variant[m]['drawdown'] <= MAX_DD for m in MODES),
        '3_ratio_not_below': all(variant[m]['ratio'] is not None and base[m]['ratio'] is not None
                                 and variant[m]['ratio'] >= base[m]['ratio'] for m in MODES),
        '4_both_halves': all(better(v, b) for v, b in zip(variant['market']['halves'], base['market']['halves'])),
        '5_double_cost': variant['double']['total'] > base['double']['total'],
    }


def config_hash():
    config = dict(cap=CAP, sleeve=SLEEVE, slots=SLOTS, margin=MARGIN, cost=COST_PCT, limit_atr=LIMIT_ATR,
                  half=HALF, tier2_bars=TIER2_BARS, tier2_filters=sorted(TIER2_FILTERS), variants=VARIANTS,
                  max_dd=MAX_DD)
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def study(routes, m15):
    mid = midpoint(routes)
    runs = {v: {m: run(routes, m15, v, mode=m) for m in MODES} | {'double': run(routes, m15, v, cost_mult=2.0)}
            for v in VARIANTS}
    base_signals = {(t['route'], t['signal']) for t in runs['B']['market'][0] if t['tier'] == 1}
    results = {v: {k: summary(*r, mid, None if v == 'B' else base_signals) for k, r in modes.items()}
               for v, modes in runs.items()}
    verdicts = {}
    for v in VARIANTS[1:]:
        c = criteria(results[v], results['B'])
        verdicts[v] = dict(criteria=c, passed=all(c.values()))
    return dict(midpoint=mid, results=results, verdicts=verdicts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    raw = open(args.input, 'rb').read()
    routes, m15 = load(json.loads(raw))
    report = dict(input_sha256=hashlib.sha256(raw).hexdigest(), config_sha256=config_hash(), **study(routes, m15))
    with open(args.output, 'w') as f:
        json.dump(report, f, indent=1, default=str)
    for v, modes in report['results'].items():
        for k, s in modes.items():
            print(f"{v:<3} {k:<7} total={s['total']:+6.1f}% DD={s['drawdown']:4.1f}% ratio={s['ratio'] or 0:5.2f} "
                  f"pos={s['positions']:>3} half={s['half_size']:>3} perp={s['perp']:+5.1f} spot={s['spot']:+5.1f} "
                  f"halves={s['halves'][0]:+5.1f}/{s['halves'][1]:+5.1f} extra={s.get('extra')}")
    for v, d in report['verdicts'].items():
        print(v, 'PASS' if d['passed'] else 'FAIL', d['criteria'])


if __name__ == '__main__':
    main()
