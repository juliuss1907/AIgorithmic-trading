"""Hourly signal bars derived exactly from native M15 candles, research only.

Nothing is fetched and nothing is synthesised: an H1 bar is the open of its first M15, the
extremes of its M15 set, the close of its last M15 and their summed volume. Hours the exchange
did not publish stay absent, exactly as the audited Spot outage is already handled for M15.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from intraday.replay_v2.donchian_filter_data import WARMUP
from intraday.replay_v2.donchian_spot_gap import GAP_OPENS
from intraday.replay_v2.trailing_candle import TrailingCandle

HOUR, FOUR_HOURS, M15 = timedelta(hours=1), timedelta(hours=4), timedelta(minutes=15)
POLICY = 'hourly-bars-from-native-m15-v1'

# Perp H4 windows where the native H4 candle already disagrees with its own M15 set. The study's
# native-boundary policy whitelists the price ones; 2022-07-16 differs in volume only. Anything
# else means the derivation is not trustworthy and the run stops.
KNOWN_PERP_H4_DISAGREEMENTS = frozenset(datetime(*t, tzinfo=timezone.utc) for t in (
    (2022, 7, 16, 4), (2023, 11, 10, 12), (2023, 11, 10, 16), (2024, 10, 28, 20)))


def _aligned(small, width):
    groups = defaultdict(list)
    seconds = int(width.total_seconds())
    for bar in small:
        groups[int(bar.opened_at.timestamp())//seconds*seconds].append(bar)
    return groups


def _missing_per_window(missing_opens, width):
    seconds = int(width.total_seconds())
    counts = defaultdict(int)
    for at in missing_opens:
        counts[int(at.timestamp())//seconds*seconds] += 1
    return counts


def _merge(members):
    return (members[0].open, max(b.high for b in members), min(b.low for b in members),
            members[-1].close, sum((b.volume for b in members), Decimal(0)))


def hourly_series(small, first, end, *, missing_opens=()):
    """M15 -> contiguous H1 bars; hours with no M15 stay absent, partial hours must be audited."""
    groups, missing = _aligned(small, HOUR), _missing_per_window(missing_opens, HOUR)
    bars, absent, partial = [], [], []
    for stamp in range(int(first.timestamp()), int(end.timestamp()), int(HOUR.total_seconds())):
        members = groups.pop(stamp, [])
        expected = 4-missing.get(stamp, 0)
        at = datetime.fromtimestamp(stamp, timezone.utc)
        if len(members) != expected:
            raise ValueError(f'unaudited M15 gap in hour {at.isoformat()}')
        if not members:
            absent.append(at.isoformat())
            continue
        if expected != 4:
            partial.append(at.isoformat())
        o, h, l, c, v = _merge(sorted(members, key=lambda b: b.opened_at))
        bars.append(TrailingCandle(interval='1h', opened_at=at, available_at=at+HOUR,
                                   open=o, high=h, low=l, close=c, volume=v))
    if groups:
        raise ValueError('M15 candles outside the study window')
    return tuple(bars), dict(absent_hours=absent, partial_hours=partial)


def h4_reproduction(large, small, *, missing_opens=()):
    """Aggregate M15 to H4 and compare with the native H4 candles that already feed the study."""
    groups, missing = _aligned(small, FOUR_HOURS), _missing_per_window(missing_opens, FOUR_HOURS)
    compared = skipped = 0
    disagreements = []
    for bar in large:
        stamp = int(bar.opened_at.timestamp())
        members = groups.get(stamp, [])
        if len(members) != 16-missing.get(stamp, 0) or missing.get(stamp):
            skipped += 1
            continue
        compared += 1
        if _merge(sorted(members, key=lambda b: b.opened_at)) != (bar.open, bar.high, bar.low, bar.close, bar.volume):
            disagreements.append(bar.opened_at)
    return dict(compared=compared, skipped_incomplete=skipped, disagreements=disagreements)


def build(data, symbols, start, end, *, keep_h4=False, warmup_hours=None):
    """Replace H4 with H1 for `symbols`; return the series bundle and an auditable derivation.

    `keep_h4` retains the native H4 candles for a regime gate that reads them next to the H1 bars.
    `warmup_hours` lengthens only the H1 series (default: the M15 warmup); the other series keep
    their fixed warmups, so a longer H1 history never changes the other cases' inputs."""
    warmup = timedelta(hours=warmup_hours) if warmup_hours else WARMUP['15m']
    first = start-warmup
    floors = {'4h': start-WARMUP['4h'], '15m': start-WARMUP['15m']}
    hourly, derivation = {}, {}
    for symbol in sorted(symbols):
        hourly[symbol] = {}
        for tag, rows in data[symbol].items():
            if tag.endswith('4h') and not keep_h4:
                continue
            floor = start if tag == 'mark15m' else floors['4h' if tag.endswith('4h') else '15m']
            hourly[symbol][tag] = tuple(r for r in rows if r.opened_at >= floor)
        derivation[symbol] = {}
        for market in ('spot', 'perp'):
            missing = GAP_OPENS if market == 'spot' else ()
            small = tuple(r for r in data[symbol][market+'15m'] if r.opened_at >= first)
            if not small or small[0].opened_at > first:
                raise ValueError(f'{symbol} {market}: M15 history is shorter than the {warmup_hours or 484}h warmup')
            bars, gaps = hourly_series(small, first, end, missing_opens=missing)
            check = h4_reproduction(data[symbol][market+'4h'], small, missing_opens=missing)
            unexpected = set(check['disagreements'])-(KNOWN_PERP_H4_DISAGREEMENTS if market == 'perp' else frozenset())
            if unexpected:
                raise ValueError(f'{symbol} {market}: M15 aggregation disagrees with native H4 at '
                                 f'{sorted(at.isoformat() for at in unexpected)}')
            hourly[symbol][market+'1h'] = bars
            derivation[symbol][market] = dict(
                policy=POLICY, hourly_bars=len(bars), **gaps,
                h4_compared=check['compared'], h4_skipped_incomplete=check['skipped_incomplete'],
                h4_disagreements=[at.isoformat() for at in check['disagreements']],
                synthetic_bars=0)
    return hourly, derivation
