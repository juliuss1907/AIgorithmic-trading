"""Position selection of the slot design (ADR-007): one pure function for the research replay and the live runtime.

Given the entries a bar would take and the positions already open, decide which entries are granted
(a free position, or by rotating out the weakest open position) and which are blocked, in the exact
order the replay applies them. No book, price or I/O access: every input is a value computed by the
caller at the bar's open, so the same function gives the same decision in replay and live.
"""

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)
SIDE = {'spot': 1, 'perp': -1}


@dataclass(frozen=True)
class Candidate:
    market: str
    symbol: str
    multiplier: Decimal
    adx: Decimal           # Trend strength used to rank, zero when the indicator is missing.
    volume_ratio: Decimal  # Volume over its moving average, the tie-break.


@dataclass(frozen=True)
class Held:
    market: str
    symbol: str
    strength: Decimal      # ADX while DMI still points the position's way, else zero.
    pnl: Decimal           # Price PnL at this bar's open (second tie-break), zero when not priced.
    rotatable: bool        # The market has an opening price for it this bar.


@dataclass(frozen=True)
class Step:
    kind: str                 # 'free', 'rotate' or 'blocked'
    market: str
    symbol: str
    multiplier: Decimal = ZERO
    adx: Decimal = ZERO
    reason: str | None = None            # blocked: one_position_per_coin, position_limit, rotation_margin
    victim: tuple | None = None          # rotate: (market, symbol) closed for this entry
    victim_strength: Decimal | None = None
    weakest: tuple | None = None         # rotation_margin: the weakest position that was not beaten


def adx_of(features):
    """ADX used to rank entries; a missing indicator ranks last."""
    return features['adx'] if features.get('adx') is not None else ZERO


def volume_ratio(features):
    """Volume over its moving average, the ranking tie-break."""
    return features['volume']/features['volume_ma'] if features.get('volume_ma') else ZERO


def trend_strength(features, side):
    """ADX while DMI still points the position's way (+1 long, -1 short), else zero."""
    if features.get('adx') is None or features.get('plus_di') is None or features.get('minus_di') is None:
        return ZERO
    return features['adx'] if side*(features['plus_di']-features['minus_di']) > 0 else ZERO


def select_entries(candidates, held, *, max_positions, limit_scope='total', rotation=True, margin=Decimal(5)):
    """Ordered steps. `limit_scope`: 'total' counts positions across both sleeves and allows one per coin;
    'sleeve' counts per market and lets a coin hold both. `rotation=False` skips a signal when full."""
    if limit_scope not in ('total', 'sleeve'):
        raise ValueError('limit scope is total or sleeve')

    def bucket(market):
        return 'all' if limit_scope == 'total' else market

    order = sorted(candidates, key=lambda c: (-c.adx, -c.volume_ratio, c.symbol, c.market))
    free = {b: max(0, max_positions-sum(1 for h in held if bucket(h.market) == b))
            for b in {bucket(m) for m in SIDE}}
    taken, rotated, steps = set(), set(), []
    for c in order:
        key = c.symbol if limit_scope == 'total' else (c.market, c.symbol)
        if key in taken:
            steps.append(Step('blocked', c.market, c.symbol, reason='one_position_per_coin'))
            continue
        if free[bucket(c.market)] > 0:
            free[bucket(c.market)] -= 1
            taken.add(key)
            steps.append(Step('free', c.market, c.symbol, c.multiplier, c.adx))
            continue
        if not rotation:
            steps.append(Step('blocked', c.market, c.symbol, reason='position_limit'))
            continue
        victims = [h for h in held if (h.market, h.symbol) not in rotated and h.rotatable
                   and bucket(h.market) == bucket(c.market)]
        if not victims:
            steps.append(Step('blocked', c.market, c.symbol, reason='position_limit'))
            continue
        weakest = min(victims, key=lambda h: (h.strength, h.pnl, h.symbol, h.market))
        if c.adx <= weakest.strength+margin:
            steps.append(Step('blocked', c.market, c.symbol, reason='rotation_margin', adx=c.adx,
                              weakest=(weakest.market, weakest.symbol), victim_strength=weakest.strength))
            continue
        rotated.add((weakest.market, weakest.symbol))
        taken.add(key)
        steps.append(Step('rotate', c.market, c.symbol, c.multiplier, c.adx,
                          victim=(weakest.market, weakest.symbol), victim_strength=weakest.strength))
    return steps


def pnl_gate_blocks(held_count, open_book_pnl, *, pnl_gate=True):
    """New entries wait while positions are open and the open book is not in profit."""
    return bool(pnl_gate and held_count and open_book_pnl <= 0)
