"""Setup-2 H1 early entry research (docs/setup2-h1-early-entry-research.md), pre-registered 2026-10-24.

After h = 1, 2, 3 hours of a running H4 bar, the bar so far (its first 4h M15 bars) replaces it and the live
evaluator's `_observe` runs on it. On the ADR-007 slot portfolio of `slot_replay`, against the current rule B:
- E1 enters at the next H1 open on the first hour whose partial bar closes beyond the Donchian channel with every
  filter passing, the volume extrapolated by 4/h;
- E2 does the same without the volume filter.
Both still enter as B on the H4 close when no hour triggers. An early entry takes only a slot left free after the
entries at the H4 open and never rotates.

Observations are recomputed here from the exported bars with the runtime's own code. Prices in the replay are
floats: the study compares outcomes and never sizes an order. Nothing here touches the runtime.
"""

import argparse
import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from statistics import fmean

from intraday import setup2
from intraday.replay_v2 import fast_filter_study as ff
from intraday.replay_v2 import slot_replay
from intraday.replay_v2 import sr_zone_study as srz
from intraday.replay_v2.donchian_filters import indicator_series
from intraday.replay_v2.slot_replay import Entry, Route

PROFILE = 'setup2_slots_v1'
HOURS = (1, 2, 3)
VARIANTS = ('B', 'E1', 'E2')
COINS = srz.COINS
WINDOW_BARS = 13
FEATURES = ('atr', 'adx', 'plus_di', 'minus_di', 'volume', 'volume_ma')


def number(v):
    return None if v is None else float(v)


def as_obs(o):
    """The float observation `slot_replay` reads, from a `Setup2Observation`."""
    f = o.features
    return dict({k: number(f.get(k)) for k in FEATURES}, mult=float(o.size_multiplier), donchian=o.donchian_entry,
                failed=frozenset(o.failed_filters), blockers=tuple(o.blockers), exit=o.exit)


def flags(o):
    """The flags the export carries for each bar, to check the local recomputation."""
    return [o.donchian_entry, list(o.failed_filters), list(o.blockers), o.exit, str(o.size_multiplier)]


# Early checks.

def partial(bar, quarters, h):
    """The H4 bar `bar` after h hours: its open, the first 4h M15 bars' range, last close and summed volume."""
    first = quarters[:4*h]
    return setup2.Bar(bar.opened_at, bar.available_at, bar.open, max(q.high for q in first),
                      min(q.low for q in first), first[-1].close, sum((q.volume for q in first), Decimal(0)))


def beyond(series, j, close, side):
    """Whether `close` is beyond the Donchian 25 channel of the bars before `j`."""
    window = series[j-setup2.ENTRY_WINDOWS[PROFILE]:j]
    return close > max(b.high for b in window) if side > 0 else close < min(b.low for b in window)


def observe(series, j, bar, h, m15, times, side, own_from):
    """The live evaluator on `series` with bar `j` replaced by the partial `bar`, volume extrapolated by 4/h."""
    rows = series[:j]+[bar]
    indicators = indicator_series(rows)
    indicators[j] = dict(indicators[j], volume=indicators[j]['volume']*4/h)
    return setup2._observe(rows, indicators, j, m15, times, side, PROFILE, own_from)


def passes(o, variant):
    if not o.donchian_entry or o.blockers:
        return False
    failed = set(o.failed_filters)
    if variant == 'E2':
        failed.discard('volume_ma')
    return not failed


def early_triggers(series, quarters_of, m15, side, own_from, *, start, check=observe):
    """{bar j: {variant: (h, observation)}} of the first hour of each H4 bar passing each early variant."""
    times = [b.available_at for b in m15]
    out = {}
    for j in range(max(start, setup2.ENTRY_WINDOWS[PROFILE]), len(series)):
        quarters = quarters_of(series[j])
        found = {}
        for h in HOURS:
            if len(found) == 2:
                break
            if quarters is None or any(q is None for q in quarters[:4*h]):
                continue
            bar = partial(series[j], quarters, h)
            if not beyond(series, j, bar.close, side):
                continue
            o = check(series, j, bar, h, m15, times, side, own_from)
            for v in ('E1', 'E2'):
                if v not in found and passes(o, v):
                    found[v] = (h, o)
        if found:
            out[j] = found
    return out


# Loading.

def load_route(key, item):
    """Series, float route, M15 quarters per H4 bar and the early triggers of one exported route."""
    symbol, market = key.split(':')
    side = slot_replay.SIDES[market]
    h4, m15 = setup2.bars(item['h4'], setup2.H4), setup2.bars(item['m15'], setup2.M15)
    anchor = datetime.fromisoformat(item['anchor'])
    own_from = datetime.fromisoformat(item['proxy_until']) if market == 'spot' and item['proxy_until'] else None
    series, obs = setup2.series_observations(h4, m15, side=side, anchor=anchor, profile=PROFILE,
                                             warmup_bars=item['warmup_bars'], own_from=own_from)
    mismatch = sum(a != (None if o is None else flags(o)) for a, o in zip(item['obs'], obs))
    if len(item['obs']) != len(obs) or mismatch:
        raise ValueError(f'{key}: {mismatch} observations differ from the export')
    by_open = {b.opened_at: b for b in m15}

    def quarters_of(bar):
        found = [by_open.get(bar.opened_at+k*setup2.M15) for k in range(slot_replay.QUARTERS)]
        return found if any(q is not None for q in found) else None

    start = next(i for i, o in enumerate(obs) if o is not None)
    triggers = early_triggers(series, quarters_of, m15, side, own_from, start=start)
    bars = tuple((b.opened_at.isoformat(), float(b.open), float(b.high), float(b.low), float(b.close))
                 for b in series)
    route = Route(symbol, market, side, bars, tuple(None if o is None else as_obs(o) for o in obs),
                  {b[0]: i for i, b in enumerate(bars)})
    return route, quarters_of, series, triggers


def m15_rows(quarters):
    return [None if q is None else (q.opened_at.isoformat(), float(q.open), float(q.high), float(q.low),
                                    float(q.close)) for q in quarters]


def load(raw):
    """Routes, M15 windows and early triggers of an export (raw JSON)."""
    routes, m15, triggers = {}, {}, {}
    for key, item in raw['routes'].items():
        route, quarters_of, series, found = load_route(key, item)
        routes[key], triggers[key] = route, found
        for t, s in enumerate(route.obs):
            if srz.b_entry(s):   # limit windows of rule B: the 13 H4 bars after the breakout
                window = []
                for k in range(t+1, min(t+1+WINDOW_BARS, len(series))):
                    window += quarters_of(series[k]) or [None]*slot_replay.QUARTERS
                m15[srz.window(route, t)] = m15_rows(window)
        for j in found:
            m15[early_key(route, j)] = m15_rows(quarters_of(series[j]))
    return routes, m15, triggers


def early_key(route, j):
    return f'{route.symbol}:{route.market}:{route.bars[j][0]}:early'


# Plans and runs.

def plans(route, triggers, variant, mode='market'):
    """{closed bar index: Entry or (Entry, early Entry)} of one route under one variant and entry mode."""
    out = {}
    for t, s in enumerate(route.obs):
        found = []
        if srz.b_entry(s):
            limit = route.bars[t][4]+ff.LIMIT_ATR*s['atr'] if route.market == 'perp' and mode == 'limit' else None
            found.append(Entry(1.0, t, limit=limit, window_key=srz.window(route, t)))
        if variant != 'B' and variant in triggers.get(t+1, {}):
            h, o = triggers[t+1][variant]
            found.append(Entry(1.0, t+1, obs=as_obs(o), early=4*h, window_key=early_key(route, t+1)))
        if found:
            out[t] = tuple(found)
    return out


def run(routes, m15, triggers, variant, *, mode='market', cost_mult=1.0):
    table = {rt: plans(r, triggers[rt], variant, mode) for rt, r in routes.items()}
    return slot_replay.run(routes, m15, lambda rt, i: table[rt].get(i), cost_mult=cost_mult)


def without_late_zec(routes, triggers):
    keep = {rt: srz.cut(r, srz.LATE_ZEC) if r.symbol == 'ZECUSDT' else r for rt, r in routes.items()}
    return keep, {rt: {j: v for j, v in triggers[rt].items() if j < len(keep[rt].bars)} for rt in keep}


def without_coin(routes, triggers, coin):
    keep = {rt: r for rt, r in routes.items() if r.symbol != coin}
    return keep, {rt: triggers[rt] for rt in keep}


# Scoring.

def group(trades):
    return dict(n=len(trades), pnl=sum(t['pnl'] for t in trades),
                mean_ret=fmean(t['ret'] for t in trades) if trades else None,
                win_rate=sum(t['ret'] > 0 for t in trades)/len(trades) if trades else None)


def early_trades(routes, m15, triggers, trades, variant):
    """First tranches that filled early: those whose entry is the planned early fill of their breakout."""
    fills = {}
    for rt, found in triggers.items():
        r = routes[rt]
        for j, by_variant in found.items():
            if variant in by_variant:
                h = by_variant[variant][0]
                fills[(rt, r.bars[j][0])] = (j, h, m15[early_key(r, j)][4*h][1])
    out = []
    for t in trades:
        hit = fills.get((t['route'], t['signal']))
        if t['tier'] == 1 and hit is not None and t['entry'] == hit[2]:
            out.append((t, *hit))
    return out


def diagnostics(routes, m15, triggers, runs):
    """Early triggers by hour, early fills against rule B's fill of the same bar, and stop exits."""
    out = {}
    for v in VARIANTS[1:]:
        planned = Counter(found[v][0] for tr in triggers.values() for found in tr.values() if v in found)
        confirmed, unconfirmed, gain_pct, gain_atr, hours = [], [], [], [], Counter()
        for t, j, h, fill in early_trades(routes, m15, triggers, runs[v][0], v):
            r = routes[t['route']]
            hours[h] += 1
            if srz.b_entry(r.obs[j]) and j+1 < len(r.bars):
                confirmed.append(t)
                b_fill = r.bars[j+1][1]
                gain_pct.append(r.side*(b_fill/fill-1)*100)
                gain_atr.append(r.side*(b_fill-fill)/float(triggers[t['route']][j][v][1].atr))
            else:
                unconfirmed.append(t)
        out[v] = dict(planned_by_hour=dict(planned), filled_by_hour=dict(hours), confirmed=group(confirmed),
                      unconfirmed=group(unconfirmed),
                      price_gain_pct=fmean(gain_pct) if gain_pct else None,
                      price_gain_atr=fmean(gain_atr) if gain_atr else None,
                      blocked={k: n for k, n in runs[v][1].items() if k.startswith('early_')})
    out['stop_exit_share'] = {v: (lambda f: sum(t['reason'] in ('stop', 'gap') for t in f)/len(f) if f else None)(
        [t for t in trades if t['tier'] == 1]) for v, (trades, _) in runs.items()}
    return out


def config_hash():
    config = dict(profile=PROFILE, hours=HOURS, variants=VARIANTS, window=WINDOW_BARS, late_zec=srz.LATE_ZEC,
                  replay=[slot_replay.CAP, slot_replay.SLEEVE, slot_replay.SLOTS, slot_replay.MARGIN,
                          slot_replay.COST_PCT, ff.LIMIT_ATR, ff.MAX_DD])
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def study(routes, m15, triggers):
    mid = ff.midpoint(routes)
    total = lambda rs, tr, v: sum(t['pnl'] for t in run(rs, m15, tr, v)[0])
    late = without_late_zec(routes, triggers)
    raw, results = {}, {}
    for v in VARIANTS:
        modes = ff.MODES if v == 'B' else ('market',)
        raw[v] = {m: run(routes, m15, triggers, v, mode=m) for m in modes} | {
            'double': run(routes, m15, triggers, v, cost_mult=2.0)}
        raw[v].setdefault('limit', raw[v]['market'])
    base_signals = {(t['route'], t['signal']) for t in raw['B']['market'][0] if t['tier'] == 1}
    for v, modes in raw.items():
        results[v] = {k: ff.summary(*r, mid, None if v == 'B' else base_signals) for k, r in modes.items()}
        results[v]['no_late_zec'] = total(*late, v)
        results[v]['without'] = {coin: total(*without_coin(routes, triggers, coin), v) for coin in COINS
                                 if any(r.symbol == coin for r in routes.values())}
    verdicts = {}
    for v in VARIANTS[1:]:
        c = srz.criteria(results[v], results['B'])
        verdicts[v] = dict(criteria=c, passed=all(c.values()))
    passed = [v for v in VARIANTS[1:] if verdicts[v]['passed']]
    chosen = max(passed, key=lambda v: results[v]['market']['ratio'] or 0) if passed else None
    markets = {v: raw[v]['market'] for v in VARIANTS}
    return dict(midpoint=mid, diagnostics=diagnostics(routes, m15, triggers, markets), results=results,
                verdicts=verdicts, chosen=chosen)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', required=True, help='export JSON, gzipped or not')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    raw = open(args.input, 'rb').read()
    text = gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw
    routes, m15, triggers = load(json.loads(text))
    report = dict(input_sha256=hashlib.sha256(text).hexdigest(), config_sha256=config_hash(),
                  **study(routes, m15, triggers))
    with open(args.output, 'w') as f:
        json.dump(report, f, indent=1, default=str)
    for v, modes in report['results'].items():
        for k in ('market', 'limit', 'double'):
            s = modes[k]
            print(f"{v:<3} {k:<7} total={s['total']:+6.1f}% DD={s['drawdown']:4.1f}% ratio={s['ratio'] or 0:5.2f} "
                  f"pos={s['positions']:>3} win={s['win_rate'] or 0:.3f} perp={s['perp']:+5.1f} "
                  f"spot={s['spot']:+5.1f} halves={s['halves'][0]:+5.1f}/{s['halves'][1]:+5.1f}")
        without = {c: round(x, 1) for c, x in modes['without'].items()}
        print(f"    no_late_zec={modes['no_late_zec']:+6.1f}  without={without}")
    for v, d in report['verdicts'].items():
        print(v, 'PASS' if d['passed'] else 'FAIL', d['criteria'])
    print('chosen:', report['chosen'])
    print(json.dumps(report['diagnostics'], indent=1, default=str))


if __name__ == '__main__':
    main()
