"""Live Setup-2 evaluator (ADR-006): the research Donchian/filter math on stored closed bars.

No indicator is reimplemented here. Signals reuse `replay_v2.donchian_filters` and the stop
reuses `ATRTrail`, so a live decision equals the research decision on the same bars.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from intraday.replay_v2.donchian_filter_book import ATRTrail
from intraday.replay_v2.donchian_filters import entry_filters, indicator_series, observation, volume_profile

EVALUATOR_VERSION = 'setup2-live-v1'
ENTRY, EXIT, ADX_THRESHOLD, FILTER_LEVEL = 30, 10, 20, 4
WARMUP_BARS, PROFILE_BARS = 600, 1920
H4, M15 = timedelta(hours=4), timedelta(minutes=15)
PROFILE_WINDOW = timedelta(hours=480)
STOP_CAP = Decimal('.10')
ZERO, ONE = Decimal(0), Decimal(1)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Bar:
    opened_at: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def bars(rows, width):
    """Binance kline rows (strings as published) to closed bars of exactly one width."""
    result = []
    for row in rows:
        opened = datetime.fromtimestamp(int(row[0])/1000, timezone.utc)
        available = datetime.fromtimestamp((int(row[6])+1)/1000, timezone.utc)
        if available-opened != width:
            raise ValueError('candle width differs from the requested interval')
        result.append(Bar(opened, available, *(Decimal(str(v)) for v in row[1:6])))
    return result


def contiguous(series, width):
    return all(b.opened_at-a.opened_at == width for a, b in zip(series, series[1:]))


def floor_h4(at):
    return EPOCH+(at-EPOCH)//H4*H4


@dataclass(frozen=True)
class Setup2Observation:
    side: int
    bar_open: datetime | None
    bar_close: datetime | None
    entry: bool
    exit: bool
    donchian_entry: bool
    failed_filters: tuple = ()
    blockers: tuple = ()
    close: Decimal | None = None
    atr: Decimal | None = None
    size_multiplier: Decimal = ZERO
    profile: dict | None = None
    features: dict = field(default_factory=dict)
    evaluator_version: str = EVALUATOR_VERSION

    def payload(self):
        text = lambda v: None if v is None else str(v)
        return dict(side=self.side, bar_open=text(self.bar_open and self.bar_open.isoformat()),
                    bar_close=text(self.bar_close and self.bar_close.isoformat()), entry=self.entry,
                    exit=self.exit, donchian_entry=self.donchian_entry, failed_filters=list(self.failed_filters),
                    blockers=list(self.blockers), close=text(self.close), atr=text(self.atr),
                    size_multiplier=str(self.size_multiplier), profile=self.profile,
                    features={k: text(v) for k, v in self.features.items()},
                    evaluator_version=self.evaluator_version)


def anchored(h4, anchor):
    """Closed H4 bars from the anchor; indicators depend on where the series starts."""
    series = [b for b in h4 if b.opened_at >= anchor]
    if not series or series[0].opened_at != anchor or not contiguous(series, H4):
        return None
    return series


def evaluate_setup2(h4, m15, *, side, anchor):
    """Signal on the last closed H4 bar. Missing data blocks entries but never exits."""
    if side not in (1, -1):
        raise ValueError('side must be +1 (Spot long) or -1 (Perp short)')
    series = anchored(h4, anchor)
    if series is None or len(series) < WARMUP_BARS:
        return Setup2Observation(side, None, None, False, False, False, blockers=('history_not_ready',))
    trigger, i = series[-1], len(series)-1
    f = indicator_series(series)[-1].copy()
    obs = observation(series[max(0, i-ENTRY):i+1], ENTRY, EXIT)
    # Profile ends at the trigger OPEN: (open-480h, open] by availability, as in research.
    window = [b for b in m15 if trigger.opened_at-PROFILE_WINDOW < b.available_at <= trigger.opened_at]
    blockers = []
    if len(window) != PROFILE_BARS or not contiguous(window, M15):
        blockers.append('profile_incomplete')
        f['profile'] = None
    else:
        profile = volume_profile(window)
        f['profile'] = {k: v for k, v in profile.items() if k != 'volumes'} if profile else None
    donchian = obs['long_entry' if side > 0 else 'short_entry']
    failed = tuple(entry_filters(f, side, FILTER_LEVEL, adx_threshold=ADX_THRESHOLD))
    atr = f['atr']
    if atr is None or not ZERO < 3*atr/trigger.close < ONE:
        blockers.append('invalid_atr_stop')
    size = min(ONE, Decimal('.02')/(atr/f['close'])) if atr else ZERO
    features = {k: v for k, v in f.items() if k != 'profile'}
    return Setup2Observation(
        side=side, bar_open=trigger.opened_at, bar_close=trigger.available_at,
        entry=donchian and not failed and not blockers, exit=obs['long_exit' if side > 0 else 'short_exit'],
        donchian_entry=donchian, failed_filters=failed, blockers=tuple(blockers), close=trigger.close,
        atr=atr, size_multiplier=size, profile=f['profile'], features=features)


def trail_stop(side, entry_price, signal_atr, h4, *, entered_at, anchor, cap=STOP_CAP):
    """Stateless ATR14×3 trail from stored bars, capped at `cap` from entry; never loosens.

    `entered_at` is the H4 slot the fill landed in (research enters at that slot's open), so
    every closed bar opened at or after it ratchets the stop with its own ATR.
    """
    trail = ATRTrail(side, Decimal(str(entry_price)), Decimal(str(signal_atr)))
    series = anchored(h4, anchor) or []
    for bar, f in zip(series, indicator_series(series)):
        if bar.opened_at >= floor_h4(entered_at) and f['atr']:
            trail.update(bar.high, bar.low, f['atr'])
    if cap is None:
        return trail.stop
    floor = Decimal(str(entry_price))*(ONE-side*cap)
    return max(trail.stop, floor) if side > 0 else min(trail.stop, floor)
