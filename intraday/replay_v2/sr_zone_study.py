"""Setup-2 support/resistance zone research (docs/setup2-sr-zone-research.md), pre-registered 2026-10-22.

Zones are the SRchannel port of `sr_zones` at its default settings, on each route's own H4 series. A rule-B
breakout has a blocking zone when its close is inside a zone or the nearest zone ahead starts within 1 ATR.
On the ADR-007 slot portfolio of `slot_replay`, against the current rule B:
- S1 skips breakouts with a blocking zone;
- S2 waits up to 6 bars for a close beyond that zone's far edge, then enters at the next open;
- S3 waits the same way, then works a limit at the cleared edge for 6 bars, stopped beyond the zone;
- S4 enters as B with the initial stop beyond the nearest zone behind the close.
Part 0 compares how often price turns away at a zone with random zones of the same width.

Prices are floats: the study compares outcomes and never sizes an order. Nothing here touches the runtime.
"""

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from statistics import fmean

from intraday.replay_v2 import fast_filter_study as ff
from intraday.replay_v2 import slot_replay, sr_zones
from intraday.replay_v2.slot_replay import CAP, Entry, Route

ZONE_ATR, WAIT_BARS, RETEST_BARS, STOP_ATR = 1.0, 6, 6, 0.25
TOUCH_BARS, TURN_ATR, PLACEBO_PCT, PLACEBO_TRIES, SEED = 3, 0.5, 0.10, 100, 20261022
VARIANTS = ('B', 'S1', 'S2', 'S3', 'S4')
COINS = ('ETHUSDT', 'HYPEUSDT', 'NEARUSDT', 'SOLUSDT', 'ZECUSDT')
LATE_ZEC = '2026-10-01'


def zone_series(route):
    return sr_zones.zones_series([b[2] for b in route.bars], [b[3] for b in route.bars])


def b_entry(s):
    return s is not None and s['donchian'] and not s['failed'] and not s['blockers']


def blocking(zones, side, close, atr):
    """The zone holding the close, else the nearest zone ahead when its near edge is under ZONE_ATR x ATR away."""
    zone = sr_zones.inside(zones, close)
    if zone is not None:
        return zone
    zone = sr_zones.ahead(zones, close, side)
    if zone is not None and (zone[0]-close if side > 0 else close-zone[1]) < ZONE_ATR*atr:
        return zone
    return None


def cleared(route, zones, t, zone):
    """S2/S3: the first bar k in t+1..t+6 closing beyond the far edge with no new blocking zone and no blocker;
    None when a Donchian exit flag comes first or the window passes."""
    side = route.side
    for k in range(t+1, min(t+WAIT_BARS, len(route.bars)-1)+1):
        s, close = route.obs[k], route.bars[k][4]
        if s is None or s['exit']:
            return None
        beyond = close > zone[1] if side > 0 else close < zone[0]
        if beyond and not s['blockers'] and s['atr'] and blocking(zones[k], side, close, s['atr']) is None:
            return k
    return None


def zone_stop(zones, side, close, atr):
    """S4: just beyond the nearest zone behind the close; None (3 ATR) without one within the 5% cap."""
    zone = sr_zones.behind(zones, close, side)
    if zone is None:
        return None
    stop = zone[0]-STOP_ATR*atr if side > 0 else zone[1]+STOP_ATR*atr
    return stop if abs(stop/close-1) <= CAP else None


def plans(route, zones, variant, mode='market'):
    """{closed bar index: Entry} of one route under one variant and entry mode."""
    out = {}

    def market_or_limit(t, at):
        s = route.obs[at]
        limit = route.bars[at][4]+ff.LIMIT_ATR*s['atr'] if route.market == 'perp' and mode == 'limit' else None
        return dict(limit=limit, window_key=window(route, t))

    for t, s in enumerate(route.obs):
        if not b_entry(s):
            continue
        close, atr = route.bars[t][4], s['atr']
        zone = blocking(zones[t], route.side, close, atr)
        if variant == 'B' or (variant in ('S1', 'S2', 'S3') and zone is None):
            out.setdefault(t, Entry(1.0, t, **market_or_limit(t, t)) if variant != 'S3' else Entry(1.0, t))
        elif variant == 'S4':
            out.setdefault(t, Entry(1.0, t, stop=zone_stop(zones[t], route.side, close, atr), **market_or_limit(t, t)))
        elif variant in ('S2', 'S3'):
            k = cleared(route, zones, t, zone)
            if k is None:
                continue
            if variant == 'S2':
                out.setdefault(k, Entry(1.0, t, rank_index=k, **market_or_limit(t, k)))
            else:
                atr_k = route.obs[k]['atr']
                edge, stop = (zone[1], zone[0]-STOP_ATR*atr_k) if route.side > 0 else (zone[0], zone[1]+STOP_ATR*atr_k)
                out.setdefault(k, Entry(1.0, t, rank_index=k, limit=edge, bars=RETEST_BARS,
                                        window_key=window(route, t), stop=stop))
    return out


def window(route, t):
    return f'{route.symbol}:{route.market}:{route.bars[t][0]}'


def run(routes, m15, zones, variant, *, mode='market', cost_mult=1.0):
    table = {rt: plans(r, zones[rt], variant, mode) for rt, r in routes.items()}
    return slot_replay.run(routes, m15, lambda rt, i: table[rt].get(i), cost_mult=cost_mult)


# Robustness subsets (criteria 6 and 7).

def cut(route, before):
    """The route's bars opened before `before`."""
    n = sum(b[0] < before for b in route.bars)
    return Route(route.symbol, route.market, route.side, route.bars[:n], route.obs[:n],
                 {b[0]: i for i, b in enumerate(route.bars[:n])})


def without_late_zec(routes, zones):
    keep = {rt: cut(r, LATE_ZEC) if r.symbol == 'ZECUSDT' else r for rt, r in routes.items()}
    return keep, {rt: zones[rt][:len(keep[rt].bars)] for rt in keep}


def without_coin(routes, zones, coin):
    keep = {rt: r for rt, r in routes.items() if r.symbol != coin}
    return keep, {rt: zones[rt] for rt in keep}


# Part 0: do prices turn at a zone?

def placebo_series(route, zones, rng):
    """A random zone of the same width per real zone, redrawn on every recompute, never overlapping a real one."""
    out, current, last = [], (), None
    for t, real in enumerate(zones):
        if real is not last:
            last, made = real, []
            for lo, hi, _ in real:
                width = hi-lo
                for _ in range(PLACEBO_TRIES):
                    mid = route.bars[t][4]*(1+rng.uniform(-PLACEBO_PCT, PLACEBO_PCT))
                    p = (mid-width/2, mid+width/2, 0)
                    if all(p[1] < z[0] or p[0] > z[1] for z in real):
                        made.append(p)
                        break
            current = tuple(made)
        out.append(current)
    return out


def touches(route, zones):
    """Outcomes of every approach of a zone known on the previous bar: 'turned', 'crossed' or 'unclear'."""
    counts = Counter()
    for t in range(1, len(route.bars)):
        prev_close, (_, _, high, low, _) = route.bars[t-1][4], route.bars[t]
        s = route.obs[t]
        if s is None or not s['atr']:
            continue
        for lo, hi, _ in zones[t-1]:
            if prev_close < lo and high >= lo:
                up = True
            elif prev_close > hi and low <= hi:
                up = False
            else:
                continue
            outcome = 'unclear'
            for k in range(t, min(t+TOUCH_BARS, len(route.bars)-1)+1):
                c = route.bars[k][4]
                if (c < lo-TURN_ATR*s['atr']) if up else (c > hi+TURN_ATR*s['atr']):
                    outcome = 'turned'
                    break
                if (c > hi) if up else (c < lo):
                    outcome = 'crossed'
                    break
            counts[outcome] += 1
    return counts


def part0(routes, zones):
    rng, rows = random.Random(SEED), {}
    for rt in sorted(routes):
        real, fake = touches(routes[rt], zones[rt]), touches(routes[rt], placebo_series(routes[rt], zones[rt], rng))
        rows[rt] = dict(real=dict(real), placebo=dict(fake))
    total = {k: sum((Counter(r[k]) for r in rows.values()), Counter()) for k in ('real', 'placebo')}
    rows['all'] = {k: dict(v) for k, v in total.items()}
    for r in rows.values():
        for k in ('real', 'placebo'):
            n = sum(r[k].values())
            r[k]['turned_rate'] = r[k].get('turned', 0)/n if n else None
    return rows


# Scoring.

def diagnostics(routes, zones, runs):
    """Blocked breakouts, S2/S3 waits and fills, S4 stop widths; `runs` maps variant to its market-mode trades."""
    blocked, waited, s4 = set(), Counter(), Counter()
    for rt, r in routes.items():
        for t, s in enumerate(r.obs):
            if not b_entry(s):
                continue
            close, atr = r.bars[t][4], s['atr']
            zone = blocking(zones[rt][t], r.side, close, atr)
            if zone is not None:
                blocked.add((rt, r.bars[t][0]))
                waited['cleared' if cleared(r, zones[rt], t, zone) is not None else 'never_cleared'] += 1
            stop = zone_stop(zones[rt][t], r.side, close, atr)
            s4['no_zone' if stop is None else 'tighter' if abs(close-stop) < 3*atr else 'wider'] += 1

    def group(trades):
        first = [t for t in trades if t['tier'] == 1]
        return dict(n=len(first), pnl=sum(t['pnl'] for t in first),
                    mean_ret=fmean(t['ret'] for t in first) if first else None,
                    win_rate=sum(t['ret'] > 0 for t in first)/len(first) if first else None)

    stop_share = {v: (lambda f: sum(t['reason'] in ('stop', 'gap') for t in f)/len(f) if f else None)(
        [t for t in trades if t['tier'] == 1]) for v, trades in runs.items()}
    return dict(blocked_breakouts=len(blocked), waits=dict(waited), s4_stops=dict(s4), stop_exit_share=stop_share,
                b_trades_on_blocked=group([t for t in runs['B'] if (t['route'], t['signal']) in blocked]),
                late_entries={v: group([t for t in runs[v] if (t['route'], t['signal']) in blocked])
                              for v in ('S2', 'S3')})


def criteria(variant, base):
    """The seven registered criteria; `variant`/`base` map 'market', 'limit', 'double', 'no_late_zec' and
    'without' (coin -> total) to results."""
    c = ff.criteria(variant, base)
    c['6_without_late_zec'] = variant['no_late_zec'] > base['no_late_zec']
    c['7_each_coin_left_out'] = all(variant['without'][coin] > base['without'][coin] for coin in base['without'])
    return c


def config_hash():
    config = dict(zone_atr=ZONE_ATR, wait=WAIT_BARS, retest=RETEST_BARS, stop_atr=STOP_ATR, touch=TOUCH_BARS,
                  turn_atr=TURN_ATR, placebo=PLACEBO_PCT, tries=PLACEBO_TRIES, seed=SEED, variants=VARIANTS,
                  late_zec=LATE_ZEC, zones=[sr_zones.PERIOD, sr_zones.WIDTH_PCT, sr_zones.RANGE_BARS,
                                            sr_zones.LOOKBACK, sr_zones.MAX_ZONES, sr_zones.MIN_STRENGTH],
                  replay=[slot_replay.CAP, slot_replay.SLEEVE, slot_replay.SLOTS, slot_replay.MARGIN,
                          slot_replay.COST_PCT, ff.LIMIT_ATR, ff.MAX_DD])
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def study(routes, m15):
    zones = {rt: zone_series(r) for rt, r in routes.items()}
    mid = ff.midpoint(routes)
    total = lambda rs, zs, v: sum(t['pnl'] for t in run(rs, m15, zs, v)[0])
    late = without_late_zec(routes, zones)
    raw, results = {}, {}
    for v in VARIANTS:
        modes = ('market',) if v == 'S3' else ff.MODES
        raw[v] = {m: run(routes, m15, zones, v, mode=m) for m in modes} | {
            'double': run(routes, m15, zones, v, cost_mult=2.0)}
        if v == 'S3':
            raw[v]['limit'] = raw[v]['market']
    base_signals = {(t['route'], t['signal']) for t in raw['B']['market'][0] if t['tier'] == 1}
    for v, modes in raw.items():
        results[v] = {k: ff.summary(*r, mid, None if v == 'B' else base_signals) for k, r in modes.items()}
        results[v]['no_late_zec'] = total(*late, v)
        results[v]['without'] = {coin: total(*without_coin(routes, zones, coin), v) for coin in COINS
                                 if any(r.symbol == coin for r in routes.values())}
    verdicts = {}
    for v in VARIANTS[1:]:
        c = criteria(results[v], results['B'])
        verdicts[v] = dict(criteria=c, passed=all(c.values()))
    passed = [v for v in VARIANTS[1:] if verdicts[v]['passed']]
    chosen = max(passed, key=lambda v: results[v]['market']['ratio'] or 0) if passed else None
    return dict(midpoint=mid, part0=part0(routes, zones),
                diagnostics=diagnostics(routes, zones, {v: raw[v]['market'][0] for v in VARIANTS}),
                results=results, verdicts=verdicts, chosen=chosen)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    raw = open(args.input, 'rb').read()
    routes, m15 = ff.load(json.loads(raw))
    report = dict(input_sha256=hashlib.sha256(raw).hexdigest(), config_sha256=config_hash(), **study(routes, m15))
    with open(args.output, 'w') as f:
        json.dump(report, f, indent=1, default=str)
    p0 = report['part0']['all']
    print(f"part0 turned: real {p0['real']['turned_rate']:.3f} of {sum(v for k, v in p0['real'].items() if k != 'turned_rate')}"
          f"  placebo {p0['placebo']['turned_rate']:.3f} of {sum(v for k, v in p0['placebo'].items() if k != 'turned_rate')}")
    for v, modes in report['results'].items():
        for k in ('market', 'limit', 'double'):
            s = modes[k]
            print(f"{v:<3} {k:<7} total={s['total']:+6.1f}% DD={s['drawdown']:4.1f}% ratio={s['ratio'] or 0:5.2f} "
                  f"pos={s['positions']:>3} perp={s['perp']:+5.1f} spot={s['spot']:+5.1f} "
                  f"halves={s['halves'][0]:+5.1f}/{s['halves'][1]:+5.1f}")
        print(f"    no_late_zec={modes['no_late_zec']:+6.1f}  without={ {c: round(x, 1) for c, x in modes['without'].items()} }")
    for v, d in report['verdicts'].items():
        print(v, 'PASS' if d['passed'] else 'FAIL', d['criteria'])
    print('chosen:', report['chosen'])
    print(json.dumps(report['diagnostics'], indent=1, default=str))


if __name__ == '__main__':
    main()
