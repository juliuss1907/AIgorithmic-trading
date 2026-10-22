"""ADR-007 slot portfolio replay shared by the Setup-2 entry studies.

3 positions, one per coin, ADX ranking, rotation margin 5 and the PnL gate, all through the runtime's
`select_entries`/`pnl_gate_blocks`; size 50% sleeve x 1/3 x the ATR multiplier; an ATR14 x 3 trail capped at 5%
from the first entry; Donchian exits at the next open; a gap through the stop exits at the open.

A study supplies `plan(route, i)`: the `Entry` taken on closed bar `i` of a free route, or None. A market entry
fills at the next H4 open; a limit entry works for `bars` H4 bars and is read on the M15 window `window_key`
(16 quarters per H4 bar from the bar after `signal_index`), or on the H4 bar itself when that window is missing.

Prices are floats: the studies compare outcomes and never size an order. Nothing here touches the runtime.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from intraday.replay_v2 import donchian_rotation_select as sel

CAP, SLEEVE, SLOTS, MARGIN = 0.05, 0.5, 3, 5
COST_PCT = {'perp': 0.20, 'spot': 0.30}   # round trip: the replay profile's fee + slippage on both sides
QUARTERS = 16
SIDES = {'perp': -1, 'spot': 1}


@dataclass(frozen=True)
class Route:
    symbol: str
    market: str
    side: int
    bars: tuple      # (opened_at, open, high, low, close)
    obs: tuple       # per bar: the observation dict, None before warm-up
    index: dict      # opened_at -> bar index


@dataclass(frozen=True)
class Entry:
    factor: float                    # size factor on top of the ATR multiplier
    signal_index: int                # the breakout bar the trade is booked under
    rank_index: int | None = None    # the bar whose ADX, multiplier and volume rank it; default the planned bar
    limit: float | None = None       # limit price; None enters at market on the next open
    bars: int = 1                    # H4 bars a limit works
    window_key: str | None = None    # M15 window of the limit
    stop: float | None = None        # initial stop; None is 3 ATR. Either is capped at 5% and trails at 3 ATR
    add: bool = False                # a second half may follow while `add_ready` holds (fast filter V2)


class Trail:
    """ATR14 x 3 trail from the first entry, capped at 5% from it; never loosens."""

    def __init__(self, side, entry, atr, stop=None):
        self.side, self.extreme = side, entry
        stop, hard = entry-side*3*atr if stop is None else stop, entry*(1-side*CAP)
        self.stop = max(stop, hard) if side > 0 else min(stop, hard)

    def update(self, high, low, atr):
        self.extreme = max(self.extreme, high) if self.side > 0 else min(self.extreme, low)
        c = self.extreme-self.side*3*atr
        self.stop = max(self.stop, c) if self.side > 0 else min(self.stop, c)

    def hit(self, high, low):
        return low <= self.stop if self.side > 0 else high >= self.stop


def run(routes, m15, plan, *, cost_mult=1.0, add_ready=None, add_bars=6):
    """Closed tranches (one per entry fill) and the blocked-entry counts of one entry plan."""
    trades, pos, pending, blocked = [], {}, {}, defaultdict(int)
    times = sorted({b[0] for r in routes.values() for b in r.bars})

    def open_(rt, price, weight, tier, signal, signal_index, factor, atr, add, stop=None):
        if rt in pos:
            pos[rt]['tranches'].append(dict(entry=price, weight=weight, tier=tier))
            return
        pos[rt] = dict(trail=Trail(routes[rt].side, price, atr, stop), signal=signal, signal_index=signal_index,
                       factor=factor, add_until=signal_index+add_bars if add else None,
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
        # 2. A second half at the open after a confirming bar inside the window.
        for rt in list(pos):
            p = pos[rt]
            if p['add_until'] is None or rt not in live:
                continue
            r, i = routes[rt], live[rt]
            if p['signal_index'] < i-1 <= p['add_until'] and add_ready(r.obs[i-1]):
                open_(rt, r.bars[i][1], p['base_weight'], 2, p['signal'], p['signal_index'], p['factor'], None, False)
                p['add_until'] = None
            elif i-1 >= p['add_until']:
                p['add_until'] = None
        # 3. Entries of the last closed bar, ranked and granted by the runtime's selection.
        busy = {routes[rt].symbol for rt in list(pos)+list(pending)}
        cands, entries = [], {}
        for rt, i in live.items():
            r = routes[rt]
            if not i or rt in pos or rt in pending or r.symbol in busy:
                continue
            e = plan(rt, i-1)
            if e is None or not e.factor:
                continue
            entries[rt] = e
            s = r.obs[i-1 if e.rank_index is None else e.rank_index]
            ratio = s['volume']/s['volume_ma'] if s['volume_ma'] else 0
            cands.append(sel.Candidate(r.market, r.symbol, Decimal(str(s['mult']*e.factor)),
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
            r, i, e = routes[rt], live[rt], entries[rt]
            s = r.obs[i-1 if e.rank_index is None else e.rank_index]
            weight, signal = SLEEVE/SLOTS*s['mult']*e.factor, r.bars[e.signal_index][0]
            if e.limit is not None:
                pending[rt] = dict(level=e.limit, atr=s['atr'], weight=weight, signal=signal,
                                   signal_index=e.signal_index, factor=e.factor, add=e.add, stop=e.stop,
                                   key=e.window_key, first=i, last=i+e.bars-1)
            else:
                open_(rt, r.bars[i][1], weight, 1, signal, e.signal_index, e.factor, s['atr'], e.add, e.stop)
        # 4. Pending limits fill on M15 inside this H4 bar; the stop is read from the fill's M15 bar on.
        for rt in list(pending):
            if rt not in live:
                continue
            p, r, i = pending[rt], routes[rt], live[rt]
            if i < p['first']:
                continue
            offset = (i-p['signal_index']-1)*QUARTERS
            quarter = (m15.get(p['key']) or [])[offset:offset+QUARTERS]
            if len(quarter) < QUARTERS or any(q is None for q in quarter):
                quarter = [(t, *r.bars[i][1:5])]   # no M15: the H4 bar stands in
            for k, (qt, qo, qh, ql, qc) in enumerate(quarter):
                if r.side < 0:
                    fill = qo if qo >= p['level'] else p['level'] if qh >= p['level'] else None
                else:
                    fill = qo if qo <= p['level'] else p['level'] if ql <= p['level'] else None
                if fill is None:
                    continue
                del pending[rt]
                open_(rt, fill, p['weight'], 1, p['signal'], p['signal_index'], p['factor'], p['atr'], p['add'],
                      p['stop'])
                filled_now.add(rt)
                rest = quarter[k:]
                if any(pos[rt]['trail'].hit(q[2], q[3]) for q in rest):
                    close(rt, qt, pos[rt]['trail'].stop, 'stop')
                else:
                    pos[rt]['after_fill'] = (max(q[2] for q in rest), min(q[3] for q in rest))
                break
            if rt in pending and i >= p['last']:
                del pending[rt]
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
