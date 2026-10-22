# SPDX-License-Identifier: MPL-2.0
"""Support and resistance zones, a port of "Support Resistance Channels" (© LonesomeTheBlue, TradingView,
Mozilla Public License 2.0, https://mozilla.org/MPL/2.0/) for docs/setup2-sr-zone-research.md.

Default settings, not tuned: pivots of 10 bars each side on high/low, a zone at most 5% of the 300-bar range
wide, pivots of the last 290 bars, the 6 strongest zones. As in the Pine script, the zones are recomputed only
on a bar that confirms a new pivot and are kept unchanged in between. Two departures:
- no zones before 300 bars, where the Pine `ta.highest(300)` is still na;
- the "broken" alerts are not ported (leaving a resistance zone downwards is reported as a support break).

Prices are floats, like the replay studies; a runtime caller converts at its boundary.
"""

PERIOD, WIDTH_PCT, RANGE_BARS, LOOKBACK, MAX_ZONES, MIN_STRENGTH = 10, 5, 300, 290, 6, 1
PIVOT_WEIGHT = 20


class ZoneTracker:
    """Feed closed bars in order; `zones` are the (lo, hi, strength) zones known at the last bar, strongest first."""

    def __init__(self):
        self.highs, self.lows = [], []
        self.pivots = []          # newest first: (price, index of the bar that confirmed it)
        self.zones = ()

    def update(self, high, low):
        self.highs.append(high)
        self.lows.append(low)
        i, j = len(self.highs)-1, len(self.highs)-1-PERIOD
        if j-PERIOD < 0:
            return self.zones
        window = [k for k in range(j-PERIOD, i+1) if k != j]
        if all(self.highs[j] > self.highs[k] for k in window):
            self.pivots.insert(0, (self.highs[j], i))
        elif all(self.lows[j] < self.lows[k] for k in window):
            self.pivots.insert(0, (self.lows[j], i))
        else:
            return self.zones
        while i-self.pivots[-1][1] > LOOKBACK:
            self.pivots.pop()
        if i+1 >= RANGE_BARS:
            self.zones = self._select(i)
        return self.zones

    def _channel(self, seed, width):
        """The zone grown from one pivot: every pivot that keeps it within `width`, in pivot order."""
        lo = hi = seed
        count = 0
        for price, _ in self.pivots:
            if (hi-price if price <= hi else price-lo) <= width:
                lo, hi = min(lo, price), max(hi, price)
                count += 1
        return lo, hi, count

    def _select(self, i):
        first = i+1-RANGE_BARS
        width = (max(self.highs[first:]) - min(self.lows[first:]))*WIDTH_PCT/100
        start = max(0, i-LOOKBACK)
        candidates = []
        for seed, _ in self.pivots:
            lo, hi, count = self._channel(seed, width)
            touches = sum(lo <= h <= hi or lo <= l <= hi
                          for h, l in zip(self.highs[start:], self.lows[start:]))
            candidates.append([count*PIVOT_WEIGHT+touches, hi, lo])
        chosen = []
        while len(chosen) < MAX_ZONES:
            best = None
            for c in candidates:                      # first of equal strengths wins, as in the Pine loop
                if c[0] >= MIN_STRENGTH*PIVOT_WEIGHT and (best is None or c[0] > best[0]):
                    best = c
            if best is None:
                break
            strength, hi, lo = best
            chosen.append((lo, hi, strength))
            for c in candidates:
                if lo <= c[1] <= hi or lo <= c[2] <= hi:
                    c[0] = -1
        return tuple(chosen)


def zones_series(highs, lows):
    """The zones known at each closed bar, from that bar and the bars before it only."""
    tracker = ZoneTracker()
    return [tracker.update(h, l) for h, l in zip(highs, lows)]


def inside(zones, price):
    """The strongest zone holding `price`, or None."""
    return next((z for z in zones if z[0] <= price <= z[1]), None)


def ahead(zones, price, side):
    """The nearest zone wholly beyond `price` in the trade's direction: above a long, below a short."""
    if side > 0:
        return min((z for z in zones if z[0] > price), key=lambda z: z[0], default=None)
    return max((z for z in zones if z[1] < price), key=lambda z: z[1], default=None)


def behind(zones, price, side):
    """The nearest zone wholly on the other side of `price`: below a long, above a short."""
    return ahead(zones, price, -side)
