"""Setup-3 research (docs/setup3-ema200-retest-research.md), pre-registered 2026-10-19.

H-A sells a pullback into EMA200 or the upper 25-bar channel inside a downtrend (Spot long mirrors it);
H-B is the EMA50/200 cross-then-retest rule of a Binance Square post, run as written. Two stages: `tuning`
picks one H-A variant per side on bars before 2026 and records it with the input and config hashes;
`holdout` runs only that choice (and H-B) on 2026 and scores the registered criteria.

Prices are floats: the study compares outcomes and never sizes an order. Nothing here touches the runtime.
"""

import argparse
import hashlib
import json
import math
from bisect import bisect_left
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import fmean, pstdev

H4 = timedelta(hours=4)
CHANNEL, SLOPE_BARS, CROSS_WINDOW, TIME_STOP = 25, 6, 30, 30
STOP_BUFFER_ATR, MIN_STOP_ATR, MAX_STOP_PCT, TARGET_R = 0.25, 0.5, 5.0, 2.0
COST_PCT = {'perp': 0.20, 'spot': 0.30}   # round trip: the replay profile's fee + slippage on both sides
FUNDING_8H_PCT = 0.01                     # funding sensitivity of a short, either sign
SPLIT, LATE_ZEC = '2026-01-01', '2026-10-01'
MIN_TUNING_TRADES, MIN_HOLDOUT_TRADES, MIN_POSITIVE_COINS, MAX_CORRELATION = 30, 20, 3, 0.5
RISK_PCT, MAX_POSITIONS = 1.0, 3          # descriptive portfolio: 1% of capital at the stop, 3 positions
QUARTERS = 16
SIDES = {'perp': -1, 'spot': 1}


@dataclass(frozen=True)
class Bar:
    at: str
    open: float
    high: float
    low: float
    close: float
    ema50: float | None = None
    ema200: float | None = None
    atr: float | None = None


@dataclass(frozen=True)
class Variant:
    hypothesis: str     # 'A' pullback into resistance, 'B' cross then retest
    zone: str = ''      # A: 'ema200' (within z ATR of EMA200) or 'channel' (the prior 25-bar extreme)
    z: float = 0.0
    target: str = ''    # A: 'channel' (the opposite 25-bar extreme) or '2r'

    @property
    def name(self):
        if self.hypothesis == 'B':
            return 'B'
        zone = f'ema200-{self.z}atr' if self.zone == 'ema200' else 'channel'
        return f'A:{zone}:{self.target}'


VARIANTS_A = tuple(Variant('A', 'ema200', z, t) for z in (0.5, 1.0) for t in ('channel', '2r')) \
    + tuple(Variant('A', 'channel', 0.0, t) for t in ('channel', '2r'))
VARIANT_B = Variant('B')


@dataclass(frozen=True)
class Trade:
    route: str
    variant: str
    side: int
    signal_at: str
    entered_at: str
    exited_at: str
    entry: float
    stop: float
    target: float | None
    exit: float
    reason: str

    @property
    def market(self):
        return self.route.split(':')[1]

    @property
    def coin(self):
        return self.route.split(':')[0]

    @property
    def risk_pct(self):
        return abs(self.entry-self.stop)/self.entry*100

    def net_pct(self, cost_mult=1.0, funding_8h=0.0):
        """Net % of the position: price move less costs; a short receives positive funding."""
        hours = (datetime.fromisoformat(self.exited_at)-datetime.fromisoformat(self.entered_at)).total_seconds()/3600
        funding = -self.side*funding_8h*hours/8 if self.market == 'perp' else 0.0
        return self.side*(self.exit/self.entry-1)*100-COST_PCT[self.market]*cost_mult+funding


# Rules on closed H4 bar i of one route; `side` is -1 for a Perp short, +1 for a Spot long.

def in_trend(bars, i, side):
    """EMA50 beyond EMA200 on the trade's side and EMA200 sloping that way over SLOPE_BARS bars."""
    if i < SLOPE_BARS:
        return False
    bar, prior = bars[i], bars[i-SLOPE_BARS]
    if None in (bar.ema50, bar.ema200, prior.ema200):
        return False
    return side*(bar.ema50-bar.ema200) > 0 and side*(bar.ema200-prior.ema200) > 0


def touched(bars, i, side, variant):
    """The bar reached the resistance (short) or support (long) zone of the variant."""
    bar = bars[i]
    if variant.zone == 'ema200':
        level = bar.ema200+side*variant.z*bar.atr
        return bar.high >= level if side < 0 else bar.low <= level
    prior = bars[i-CHANNEL:i]
    if i < CHANNEL:
        return False
    return bar.high >= max(b.high for b in prior) if side < 0 else bar.low <= min(b.low for b in prior)


def rejected(bar, side):
    """Closed back on the trend's side of EMA200, a candle of the trade's colour, in that half of its range."""
    middle = (bar.high+bar.low)/2
    return side*(bar.close-bar.ema200) > 0 and side*(bar.close-bar.open) > 0 and side*(bar.close-middle) >= 0


def crossed(bars, i, side):
    """EMA50 crossed EMA200 towards the trade's side on bar i."""
    if i < 1 or None in (bars[i].ema50, bars[i].ema200, bars[i-1].ema50, bars[i-1].ema200):
        return False
    return side*(bars[i].ema50-bars[i].ema200) > 0 and side*(bars[i-1].ema50-bars[i-1].ema200) <= 0


def retested(bar, side):
    """Touched EMA200 and closed back on the trend's side (H-B)."""
    return (bar.high >= bar.ema200 if side < 0 else bar.low <= bar.ema200) and side*(bar.close-bar.ema200) > 0


def signals(bars, side, variant, own_from=None):
    """Signal bar indices in time order. H-B gives at most one per cross: its first retest within the window."""
    usable = lambda i: bars[i].ema200 is not None and bars[i].atr and (own_from is None or bars[i].at >= own_from)
    if variant.hypothesis == 'A':
        return [i for i in range(len(bars)) if usable(i) and in_trend(bars, i, side)
                and touched(bars, i, side, variant) and rejected(bars[i], side)]
    found = []
    for c in range(len(bars)):
        if crossed(bars, c, side):
            hit = next((t for t in range(c+1, min(c+1+CROSS_WINDOW, len(bars)))
                        if usable(t) and retested(bars[t], side)), None)
            if hit is not None and (not found or hit > found[-1]):
                found.append(hit)
    return found


def levels(bars, i, side, variant, entry):
    """(stop, target) for a signal on bar i filled at `entry`, or None when the registered limits skip it."""
    bar = bars[i]
    stop = (bar.high if side < 0 else bar.low)-side*STOP_BUFFER_ATR*bar.atr
    distance = side*(entry-stop)
    if distance < MIN_STOP_ATR*bar.atr or distance/entry*100 > MAX_STOP_PCT:
        return None
    if variant.hypothesis == 'B':
        return stop, None
    if variant.target == '2r':
        target = entry+side*TARGET_R*distance
    else:
        window = bars[max(0, i-CHANNEL+1):i+1]
        target = min(b.low for b in window) if side < 0 else max(b.high for b in window)
    if side*(target-entry) <= 0:   # the opposite band is already behind the fill: no room to the target
        return None
    return stop, target


def intrabar(side, stop, target, bar, quarters):
    """Exit inside one H4 bar: ('stop'|'target', price) or None. When both are reached, M15 bars decide; an
    M15 bar reaching both, or missing M15 data, counts as the stop."""
    hits_stop = (lambda b: b.high >= stop) if side < 0 else (lambda b: b.low <= stop)
    hits_target = (lambda b: target is not None and (b.low <= target if side < 0 else b.high >= target))
    stop_hit, target_hit = hits_stop(bar), hits_target(bar)
    if stop_hit and target_hit:
        for q in quarters or ():
            if hits_stop(q):
                return 'stop', stop
            if hits_target(q):
                return 'target', target
        return 'stop', stop
    if stop_hit:
        return 'stop', stop
    if target_hit:
        return 'target', target
    return None


def simulate(route, bars, side, variant, quarters_of=lambda bar: None, own_from=None):
    """One position at a time per route: enter at the next open, exit on stop, target, time or EMA50."""
    trades, free_from = [], 0
    for i in signals(bars, side, variant, own_from):
        e = i+1
        if e >= len(bars) or e < free_from:
            continue
        entry = bars[e].open
        found = levels(bars, i, side, variant, entry)
        if found is None:
            continue
        stop, target = found
        result = None
        for j in range(e, len(bars)):
            bar = bars[j]
            if j > e:
                previous = bars[j-1]
                if side*(bar.open-stop) <= 0:
                    result = ('stop_gap', bar.open, j, j)
                elif target is not None and side*(bar.open-target) >= 0:
                    result = ('target_gap', bar.open, j, j)
                elif variant.hypothesis == 'A' and j-e >= TIME_STOP:
                    result = ('time', bar.open, j, j)
                elif variant.hypothesis == 'B' and side*(previous.close-previous.ema50) < 0:
                    result = ('ema50', bar.open, j, j)
                if result:
                    break
            hit = intrabar(side, stop, target, bar, quarters_of(bar))
            if hit:
                result = (hit[0], hit[1], j, j+1)
                break
        if result is None:
            last = len(bars)-1
            result = ('open', bars[last].close, last, len(bars))
        reason, price, at, free_from = result
        exited_at = bars[at].at if reason in ('stop_gap', 'target_gap', 'time', 'ema50') else end_of(bars[at])
        trades.append(Trade(route, variant.name, side, bars[i].at, bars[e].at, exited_at, entry, stop, target, price,
                            reason))
    return trades


def end_of(bar):
    """Close time of an H4 bar (exits inside it are stamped at its end)."""
    return (datetime.fromisoformat(bar.at)+H4).isoformat()


# Statistics, selection and criteria.

def summary(trades, cost_mult=1.0, funding_8h=0.0):
    r = [t.net_pct(cost_mult, funding_8h) for t in trades]
    if not r:
        return dict(n=0)
    rr = [t.net_pct(cost_mult, funding_8h)/t.risk_pct for t in trades]
    wins, losses = [x for x in r if x > 0], [x for x in r if x <= 0]
    se = pstdev(r)/math.sqrt(len(r)) if len(r) > 1 else float('nan')
    return dict(n=len(r), win=len(wins)/len(r), avg_win=fmean(wins) if wins else 0.0,
                avg_loss=fmean(losses) if losses else 0.0, mean=fmean(r), se=se,
                t=fmean(r)/se if se and se == se and se > 0 else float('nan'), mean_r=fmean(rr),
                profit_factor=sum(wins)/-sum(losses) if losses and sum(losses) < 0 else float('inf'),
                total=sum(r))


def breakdown(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: summary(v) for k, v in sorted(groups.items())}


def select(results):
    """Registered choice on tuning: variants with at least MIN_TUNING_TRADES; the best mean, or among those
    within one standard error of it, the one with the fewest trades."""
    eligible = {name: s for name, s in results.items() if s['n'] >= MIN_TUNING_TRADES}
    if not eligible:
        return None
    best = max(eligible.values(), key=lambda s: s['mean'])
    close = {name: s for name, s in eligible.items() if s['mean'] >= best['mean']-best['se']}
    return min(close, key=lambda name: (close[name]['n'], -close[name]['mean']))


def portfolio(trades):
    """Descriptive book: 1% of capital at the stop, at most three positions, one per coin, first come."""
    taken, book = [], []
    for t in sorted(trades, key=lambda t: (t.entered_at, t.route)):
        live = [x for x in taken if x.exited_at > t.entered_at]
        if len(live) >= MAX_POSITIONS or any(x.coin == t.coin for x in live):
            continue
        taken.append(t)
        book.append((t.exited_at, RISK_PCT*t.net_pct()/t.risk_pct))
    equity = peak = drawdown = 0.0
    monthly = defaultdict(float)
    for at, pnl in sorted(book):
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
        monthly[at[:7]] += pnl
    return dict(trades=len(book), total_pct=equity, max_drawdown_pct=drawdown), dict(monthly)


def correlation(a, b):
    months = sorted(set(a) | set(b))
    x, y = [a.get(m, 0.0) for m in months], [b.get(m, 0.0) for m in months]
    if len(months) < 3 or pstdev(x) == 0 or pstdev(y) == 0:
        return float('nan')
    mx, my = fmean(x), fmean(y)
    return sum((p-mx)*(q-my) for p, q in zip(x, y))/len(x)/pstdev(x)/pstdev(y)


def criteria(tuning, holdout, corr, coins):
    """The six registered criteria for one side; `coins` is every coin of the study."""
    t, h = summary(tuning), summary(holdout)
    late = summary([x for x in holdout if not (x.coin == 'ZECUSDT' and x.entered_at >= LATE_ZEC)])
    per_coin = {c: sum(x.net_pct() for x in tuning+holdout if x.coin == c) for c in coins}
    result = {
        '1_tuning_mean_t2': t['n'] > 1 and t['mean'] > 0 and t['t'] >= 2,
        '2_holdout_n_and_mean': ('insufficient' if h['n'] < MIN_HOLDOUT_TRADES else h['mean'] > 0),
        '3_double_cost_positive': all(summary(x, cost_mult=2)['n'] and summary(x, cost_mult=2)['mean'] > 0
                                      for x in (tuning, holdout)),
        '4_positive_coins': sum(v > 0 for v in per_coin.values()) >= MIN_POSITIVE_COINS,
        '5_holdout_without_late_zec': late['n'] > 0 and late['mean'] > 0,
        '6_correlation_below': corr == corr and corr < MAX_CORRELATION,
    }
    result['passed'] = all(v is True for v in result.values())
    return result, per_coin


# Input, hashing and the two stages.

def load(path):
    raw = json.load(open(path))
    routes = {}
    for name, route in raw['routes'].items():
        f = lambda v: None if v is None else float(v)
        bars = [Bar(r[0], *map(float, r[1:5]), *map(f, r[5:8])) for r in route['h4']]
        quarters = [Bar(r[0], *map(float, r[1:5])) for r in route['m15']]
        routes[name] = dict(bars=bars, quarters=quarters, own_from=route.get('own_from'))
    return raw, routes


def quarter_lookup(quarters):
    times = [q.at for q in quarters]

    def of(bar):
        k = bisect_left(times, bar.at)
        window = quarters[k:k+QUARTERS]
        return window if len(window) == QUARTERS and window[0].at == bar.at else None
    return of


def config_hash():
    config = dict(CHANNEL=CHANNEL, SLOPE_BARS=SLOPE_BARS, CROSS_WINDOW=CROSS_WINDOW, TIME_STOP=TIME_STOP,
                  STOP_BUFFER_ATR=STOP_BUFFER_ATR, MIN_STOP_ATR=MIN_STOP_ATR, MAX_STOP_PCT=MAX_STOP_PCT,
                  TARGET_R=TARGET_R, COST_PCT=COST_PCT, SPLIT=SPLIT, variants=[asdict(v) for v in VARIANTS_A])
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def run_all(routes, variant):
    trades = []
    for name, route in routes.items():
        side = SIDES[name.split(':')[1]]
        trades += simulate(name, route['bars'], side, variant, quarter_lookup(route['quarters']), route['own_from'])
    return trades


def by_side(trades, market, period):
    keep = (lambda t: t.entered_at < SPLIT) if period == 'tuning' else (lambda t: t.entered_at >= SPLIT)
    return [t for t in trades if t.market == market and keep(t)]


def tuning_stage(routes, input_hash):
    report = dict(stage='tuning', input_sha256=input_hash, config_sha256=config_hash(), sides={})
    for market in SIDES:
        results = {v.name: summary(by_side(run_all(routes, v), market, 'tuning')) for v in VARIANTS_A}
        b = by_side(run_all(routes, VARIANT_B), market, 'tuning')
        report['sides'][market] = dict(variants_a=results, selected_a=select(results), b=summary(b))
    return report


def holdout_stage(routes, input_hash, selection, setup2_trades):
    if selection['input_sha256'] != input_hash or selection['config_sha256'] != config_hash():
        raise ValueError('holdout must run on the tuning input and config')
    setup2_monthly = defaultdict(float)
    for t in setup2_trades:
        setup2_monthly[t['exited'][:7]] += t['pnl']
    coins = sorted({name.split(':')[0] for name in routes})
    report = dict(stage='holdout', input_sha256=input_hash, config_sha256=config_hash(), hypotheses={})
    chosen = {m: s['selected_a'] for m, s in selection['sides'].items()}
    for label in ('A', 'B'):
        sides, every = {}, []
        for market in SIDES:
            variant = VARIANT_B if label == 'B' else next((v for v in VARIANTS_A if v.name == chosen[market]), None)
            if variant is None:
                sides[market] = dict(variant=None, note='no variant reached the tuning minimum')
                continue
            trades = [t for t in run_all(routes, variant) if t.market == market]
            every += trades
            tuning, holdout = by_side(trades, market, 'tuning'), by_side(trades, market, 'holdout')
            book, monthly = portfolio(trades)
            corr = correlation(monthly, setup2_monthly)
            verdict, per_coin = criteria(tuning, holdout, corr, coins)
            sides[market] = dict(
                variant=variant.name, tuning=summary(tuning), holdout=summary(holdout),
                holdout_without_late_zec=summary([t for t in holdout
                                                  if not (t.coin == 'ZECUSDT' and t.entered_at >= LATE_ZEC)]),
                double_cost=dict(tuning=summary(tuning, 2), holdout=summary(holdout, 2)),
                funding=dict(plus=summary(tuning+holdout, funding_8h=FUNDING_8H_PCT),
                             minus=summary(tuning+holdout, funding_8h=-FUNDING_8H_PCT)) if market == 'perp' else None,
                by_coin=breakdown(tuning+holdout, lambda t: t.coin), per_coin_net=per_coin,
                by_year=breakdown(tuning+holdout, lambda t: t.entered_at[:4]),
                exits=breakdown(tuning+holdout, lambda t: t.reason), portfolio=book, correlation_setup2=corr,
                criteria=verdict)
        book, monthly = portfolio(every)
        losing = [m for m, v in setup2_monthly.items() if v < 0]
        overlap = sum(1 for t in every for s in setup2_trades if s['route'].split(':')[0] == t.coin
                      and s['entered'] < t.exited_at and t.entered_at < s['exited'])
        report['hypotheses'][label] = dict(
            sides=sides, portfolio=book, correlation_setup2=correlation(monthly, setup2_monthly),
            pnl_in_setup2_losing_months=sum(monthly.get(m, 0.0) for m in losing), setup2_losing_months=len(losing),
            overlapping_setup2_trades=overlap)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('stage', choices=('tuning', 'holdout'))
    parser.add_argument('--input', required=True, help='JSON from scripts/setup3_export.py')
    parser.add_argument('--output', required=True)
    parser.add_argument('--selection', help='holdout: the tuning report')
    parser.add_argument('--setup2-trades', help='holdout: Setup-2 slot replay trades (route, entered, exited, pnl)')
    args = parser.parse_args(argv)
    input_hash = hashlib.sha256(open(args.input, 'rb').read()).hexdigest()
    _, routes = load(args.input)
    if args.stage == 'tuning':
        report = tuning_stage(routes, input_hash)
    else:
        if not args.selection or not args.setup2_trades:
            parser.error('holdout needs --selection and --setup2-trades')
        report = holdout_stage(routes, input_hash, json.load(open(args.selection)), json.load(open(args.setup2_trades)))
    with open(args.output, 'w') as handle:
        json.dump(report, handle, indent=2, default=str)
    print(json.dumps(report, indent=1, default=str)[:20000])


if __name__ == '__main__':
    main()
