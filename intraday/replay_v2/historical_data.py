"""Public Futures history frozen separately from Spot/runtime evidence."""

from datetime import datetime, timedelta, timezone
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from pydantic import Field, field_validator, model_validator

from intraday.assets import ticker_symbol
from intraday.replay_v2.contracts import Candle, FrozenModel, utc
from intraday.replay_v2.funding import NoRedirect
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.portfolio_research import daily_points


BASE = 'https://fapi.binance.com'
PATHS = {'trade': '/fapi/v1/klines', 'mark': '/fapi/v1/markPriceKlines'}
WIDTHS = {'4h': timedelta(hours=4), '1d': timedelta(days=1)}
MAX_BYTES = 20_000_000


def public_candle_json(path, query):
    if path not in PATHS.values():
        raise ValueError('unsupported public Futures history path')
    request = Request(BASE+path+'?'+urlencode(query), headers={'User-Agent': 'aigt-historical-research/1'})
    for attempt in range(3):
        try:
            with build_opener(NoRedirect()).open(request, timeout=15) as response:
                body = response.read(MAX_BYTES+1)
            if len(body) > MAX_BYTES:
                raise ValueError('Futures history response exceeds size limit')
            return json.loads(body)
        except HTTPError as error:
            if attempt == 2 or error.code not in {429, 500, 502, 503, 504}:
                raise
            time.sleep(min(5, max(1, int(error.headers.get('Retry-After', 2)))))
        except URLError:
            if attempt == 2:
                raise
            time.sleep(attempt+1)


def validate_rows(rows, interval, start, end):
    width = WIDTHS[interval]
    width_ms = int(width.total_seconds()*1000)
    expected = list(range(int(start.timestamp()*1000), int(end.timestamp()*1000), width_ms))
    if any(len(row) < 7 or isinstance(row[0], bool) or row[0] != int(row[0]) or
           row[0] % width_ms or row[6] != row[0]+width_ms-1 for row in rows):
        raise ValueError('invalid native Futures timestamps or interval')
    if [row[0] for row in rows] != expected:
        raise ValueError('incomplete native Futures window, gap or duplicate')
    if interval == '4h':
        return tuple(Candle.from_row(row) for row in rows)
    daily_points(rows)
    return ()


class CandleSnapshot(FrozenModel):
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    symbol: str
    interval: str
    price_kind: str
    source: str
    coverage_start: datetime
    coverage_end: datetime
    fetched_at: datetime
    pages: int = Field(ge=1)
    raw_rows: tuple[tuple, ...]

    _symbol = field_validator('symbol')(ticker_symbol)
    _times = field_validator('coverage_start', 'coverage_end', 'fetched_at')(utc)

    @model_validator(mode='after')
    def verify(self):
        if fingerprint(self.model_dump(mode='json', exclude={'snapshot_id'})) != self.snapshot_id:
            raise ValueError('Futures candle snapshot checksum mismatch')
        if self.interval not in WIDTHS or self.price_kind not in PATHS or self.source != BASE+PATHS[self.price_kind]:
            raise ValueError('invalid Futures snapshot source or interval')
        width_ms = int(WIDTHS[self.interval].total_seconds()*1000)
        if not self.coverage_start < self.coverage_end <= self.fetched_at or any(
            int(at.timestamp()*1000) % width_ms or at.microsecond for at in (self.coverage_start, self.coverage_end)):
            raise ValueError('Futures snapshot requires completed aligned coverage')
        validate_rows(self.raw_rows, self.interval, self.coverage_start, self.coverage_end)
        return self

    def candles(self):
        return validate_rows(self.raw_rows, self.interval, self.coverage_start, self.coverage_end)


def fetch_candle_snapshot(symbol, interval, start, end, *, price_kind='trade',
                          fetch_json=public_candle_json, page_size=1000, now=None):
    symbol, start, end = ticker_symbol(symbol), utc(start), utc(end)
    observed = utc(now or datetime.now(timezone.utc))
    if interval not in WIDTHS or price_kind not in PATHS or not 1 <= page_size <= 1500:
        raise ValueError('invalid Futures collection parameters')
    width_ms = int(WIDTHS[interval].total_seconds()*1000)
    if not start < end <= observed or any(int(at.timestamp()*1000) % width_ms or at.microsecond for at in (start, end)):
        raise ValueError('Futures collection requires aligned completed past window')
    cursor, end_ms = int(start.timestamp()*1000), int(end.timestamp()*1000)
    rows, pages = [], 0
    while cursor < end_ms:
        page = fetch_json(PATHS[price_kind], {'symbol': symbol, 'interval': interval,
            'startTime': cursor, 'endTime': end_ms-1, 'limit': page_size})
        pages += 1
        if not isinstance(page, list) or len(page) > page_size or not page:
            raise ValueError('incomplete Futures history response')
        last = cursor+len(page)*width_ms
        validate_rows(page, interval, datetime.fromtimestamp(cursor/1000, timezone.utc),
                      datetime.fromtimestamp(last/1000, timezone.utc))
        if last > end_ms:
            raise ValueError('Futures response outside requested window')
        rows.extend(page)
        cursor = last
        if pages >= 10_000:
            raise ValueError('Futures pagination limit exceeded')
        # Explicit collectors are serial and modestly paced, never inside replay.
        if fetch_json is public_candle_json and cursor < end_ms:
            time.sleep(.1)
    payload = dict(symbol=symbol, interval=interval, price_kind=price_kind,
        source=BASE+PATHS[price_kind], coverage_start=start.isoformat().replace('+00:00', 'Z'),
        coverage_end=end.isoformat().replace('+00:00', 'Z'),
        fetched_at=observed.isoformat().replace('+00:00', 'Z'), pages=pages, raw_rows=rows)
    return CandleSnapshot(snapshot_id=fingerprint(payload), **payload)
