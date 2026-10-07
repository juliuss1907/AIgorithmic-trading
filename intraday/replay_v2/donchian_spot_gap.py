"""Explicit, source-preserving policy for the audited Binance Spot M15 gap.

No bars are generated and no generic native-series validation is weakened.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import model_validator

from intraday.replay_v2.donchian_filter_data import FilterSnapshot
from intraday.replay_v2.intraday_timeframes import IntradayCandle
from intraday.replay_v2.intraday_data import verify_boundaries
from decimal import Decimal


POLICY = 'binance-spot-m15-audited-20230324-v1'
WIDTH = timedelta(minutes=15)
GAP_OPENS = tuple(datetime(2023, 3, 24, 12, 45, tzinfo=timezone.utc)+i*WIDTH for i in range(5))
PARTIAL_CLOSES = {
    'BTCUSDT': {1640321100000:1640321994362, 1679661000000:1679661581646},
    'ETHUSDT': {1640321100000:1640321996158, 1679661000000:1679661583061},
    'SOLUSDT': {1679661000000:1679661586948},
}
BOUNDARY_OPEN=datetime(2023,3,24,12,tzinfo=timezone.utc)
BOUNDARY_PRICES={'BTCUSDT':(Decimal('28079.99'),Decimal('28080')),
                 'ETHUSDT':(Decimal('1789.51'),Decimal('1789.52'))}


def verify_spot_boundaries(symbol,large,small,start,end,label):
    """Exactly two approved native open differences, never a price tolerance."""
    opens={b.opened_at:b.open for b in small}
    closes={b.available_at:b.close for b in small}
    retained=[]
    for bar in large:
        if (start<=bar.opened_at<end and bar.opened_at==BOUNDARY_OPEN and symbol in BOUNDARY_PRICES and
                (opens.get(bar.opened_at)!=bar.open or closes.get(bar.available_at)!=bar.close)):
            if (bar.open,opens.get(bar.opened_at))!=BOUNDARY_PRICES[symbol] or closes.get(bar.available_at)!=bar.close:
                raise ValueError('unapproved Spot boundary price difference')
        else:
            retained.append(bar)
    verify_boundaries(symbol,retained,small,start,end,label)


class SourceSpotCandle(IntradayCandle):
    @model_validator(mode='after')
    def valid_bar(self):
        if not timedelta(0) < self.available_at-self.opened_at <= WIDTH or (
                self.opened_at.microsecond or int(self.opened_at.timestamp()) % 900):
            raise ValueError('invalid source Spot M15 interval')
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError('invalid OHLC bounds')
        return self


def spot_candles(rows, symbol, start, end):
    if symbol not in PARTIAL_CLOSES or not start < end or any(
            at.microsecond or int(at.timestamp()) % 900 for at in (start, end)):
        raise ValueError('invalid audited Spot identity/coverage')
    expected = []
    at = start
    while at < end:
        if at not in GAP_OPENS:
            expected.append(int(at.timestamp()*1000))
        at += WIDTH
    if [r[0] for r in rows] != expected:
        raise ValueError('Spot missing/duplicate bars outside the approved source gap')
    candles = []
    for r in rows:
        if len(r) < 7 or isinstance(r[0], bool) or isinstance(r[6], bool) or r[0] != int(r[0]):
            raise ValueError('invalid native Spot row')
        if r[6] != PARTIAL_CLOSES[symbol].get(r[0], r[0]+899999):
            raise ValueError('Spot closeTime not in approved source whitelist')
        candles.append(SourceSpotCandle(interval='15m',
            opened_at=datetime.fromtimestamp(r[0]/1000, timezone.utc),
            available_at=datetime.fromtimestamp((r[6]+1)/1000, timezone.utc),
            open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]))
    return tuple(candles)


class SparseSpotSnapshot(FilterSnapshot):
    data_policy: Literal['binance-spot-m15-audited-20230324-v1'] = POLICY

    def candles(self):
        if self.market != 'spot' or self.interval != '15m':
            raise ValueError('audited gap policy applies only to Spot M15')
        return spot_candles(self.raw_rows, self.symbol, self.coverage_start, self.coverage_end)


def missing_profile_count(start, end):
    return sum(start < at+WIDTH <= end for at in GAP_OPENS)


def stale_symbols(at, symbols):
    return [s for s in sorted(symbols) if datetime.fromtimestamp(
        (PARTIAL_CLOSES[s][1679661000000]+1)/1000, timezone.utc) <= at < GAP_OPENS[-1]+WIDTH]


def availability(samples):
    return dict(data_policy=POLICY, synthetic_bars=0,
        missing_opens=[at.isoformat() for at in GAP_OPENS],
        partial_close_times_ms=PARTIAL_CLOSES,
        approved_boundary_open_differences={s:dict(at=BOUNDARY_OPEN.isoformat(),
            h4_open=str(prices[0]),m15_open=str(prices[1]),original_prices_unchanged=True) for s,prices in BOUNDARY_PRICES.items()},
        stale_valuation_samples=sum(bool(row.get('stale_spot_symbols')) for row in samples),
        execution='No Spot fills while unavailable; pending exits at next actual Spot open',
        valuation='Last observed Spot price, explicitly stale during source unavailability')


def load_approved(audit_path, start, end):
    """Reuse immutable audited raw rows, not a fetched or imputed substitute."""
    from pathlib import Path
    from intraday.replay_v2.donchian_filter_data import SPOT_SOURCE, WARMUP, verify_files
    from intraday.replay_v2.historical_study import read_inputs
    from intraday.replay_v2.metrics import fingerprint
    from intraday.replay_v2.portfolio_study import file_hash

    audit_path = Path(audit_path).expanduser().resolve()
    audit = read_inputs(audit_path)
    items = audit['audits']
    if len(items) != 3 or {i['symbol'] for i in items} != set(PARTIAL_CLOSES):
        raise ValueError('requires all three checksum-bound Spot M15 sources')
    hashes = {str(audit_path):file_hash(audit_path), **{i['raw_path']:i['raw_sha256'] for i in items}}
    verify_files(hashes)
    snapshots = {}
    for item in items:
        raw = read_inputs(item['raw_path'])
        if (raw['symbol'] != item['symbol'] or raw['source'] != SPOT_SOURCE or
                raw['window'] != dict(start_ms=int((start-WARMUP['15m']).timestamp()*1000),
                                      end_ms=int(end.timestamp()*1000)) or
                raw.get('accepted_snapshot') is not False):
            raise ValueError('audited raw Spot M15 identity mismatch')
        payload = dict(market='spot', symbol=item['symbol'], interval='15m', source=SPOT_SOURCE,
            coverage_start=(start-WARMUP['15m']).isoformat().replace('+00:00','Z'),
            coverage_end=end.isoformat().replace('+00:00','Z'),
            fetched_at=datetime.fromisoformat(raw['fetched_at']).isoformat().replace('+00:00','Z'),
            pages=raw['pages'], raw_rows=raw['raw_rows'], data_policy=POLICY)
        snapshots[item['symbol']] = SparseSpotSnapshot(snapshot_id=fingerprint(payload), **payload)
    verify_files(hashes)
    return snapshots, availability([]), hashes
