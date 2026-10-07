"""User-approved NEAR/ZEC source exceptions; original three-coin policies stay intact."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import Field

from intraday.replay_v2.donchian_filter_data import FilterSnapshot
from intraday.replay_v2.donchian_spot_gap import SourceSpotCandle, GAP_OPENS
from intraday.replay_v2.donchian_mark_gap import SourceMarkCandle, GAP_OPEN
from intraday.replay_v2.historical_data import validate_rows
from intraday.replay_v2.intraday_data import verify_boundaries
from intraday.replay_v2.donchian_adx_boundaries import verify_join_boundaries, disclosure as old_disclosure


POLICY = 'approved-near-zec-native-source-20261005-v1'
SYMBOLS = {'NEARUSDT', 'ZECUSDT'}
PARTIAL_CLOSES = {
    'NEARUSDT': {1640321100000:1640321998665, 1679661000000:1679661585395},
    'ZECUSDT': {1679661000000:1679661588463},
}
WARMUP_OPEN = 1632888000000
WARMUP_CLOSE = 1632898799999
CANONICAL_CLOSE = 1632902399999
PRICE_DIFFERENCES = {
    ('perp','NEARUSDT','2023-11-10T12:00:00+00:00'): (None, ('1.4300','1.4360')),
    ('perp','NEARUSDT','2024-10-28T20:00:00+00:00'): (('4.2500','4.2260'), None),
    ('spot','ZECUSDT','2023-03-24T12:00:00+00:00'): (('36.50000000','36.60000000'), None),
    ('perp','ZECUSDT','2023-11-10T12:00:00+00:00'): (None, ('29.32','29.60')),
    ('perp','ZECUSDT','2024-10-28T20:00:00+00:00'): (('38.09','38.08'), None),
}


class FiveSpotCandle(SourceSpotCandle):
    """Marker for separately approved added-coin availability."""


class FiveMarkCandle(SourceMarkCandle):
    """Marker for separately approved added-coin mark availability."""


def sparse_rows(rows, symbol, start, end, missing):
    if symbol not in SYMBOLS or not start<end or any(
            at.microsecond or int(at.timestamp())%900 for at in (start,end)):
        raise ValueError('invalid approved five-coin source identity/coverage')
    excluded={int(at.timestamp()*1000) for at in missing}
    if any(not isinstance(r,(list,tuple)) or len(r)<7 or isinstance(r[0],bool) or not isinstance(r[0],int) for r in rows):
        raise ValueError('invalid added-coin native timestamps')
    expected=[at for at in range(int(start.timestamp()*1000),int(end.timestamp()*1000),900000) if at not in excluded]
    if [r[0] for r in rows]!=expected:
        raise ValueError('new coin gap/duplicate outside separately approved source gaps')


def spot_candles(rows, symbol, start, end):
    sparse_rows(rows,symbol,start,end,GAP_OPENS)
    result=[]
    for row in rows:
        if len(row)<7 or isinstance(row[6],bool) or row[6]!=PARTIAL_CLOSES[symbol].get(row[0],row[0]+899999):
            raise ValueError('new coin closeTime outside separately approved exact metadata')
        result.append(FiveSpotCandle(interval='15m',opened_at=datetime.fromtimestamp(row[0]/1000,timezone.utc),
            available_at=datetime.fromtimestamp((row[6]+1)/1000,timezone.utc),
            open=row[1],high=row[2],low=row[3],close=row[4],volume=row[5]))
    return tuple(result)


def mark_candles(rows, symbol, start, end):
    sparse_rows(rows,symbol,start,end,(GAP_OPEN,))
    if any(len(r)<7 or isinstance(r[6],bool) or r[6]!=r[0]+899999 for r in rows):
        raise ValueError('unapproved new coin mark metadata')
    return tuple(FiveMarkCandle.from_row(r,'15m') for r in rows)


class ApprovedFiveSnapshot(FilterSnapshot):
    data_policy: Literal['approved-near-zec-native-source-20261005-v1'] = POLICY
    original_source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    def candles(self):
        if self.symbol not in SYMBOLS:
            raise ValueError('new source policy cannot replace old coin policies')
        if (self.market,self.interval)==('spot','15m'):
            return spot_candles(self.raw_rows,self.symbol,self.coverage_start,self.coverage_end)
        if (self.market,self.interval)==('mark','15m'):
            return mark_candles(self.raw_rows,self.symbol,self.coverage_start,self.coverage_end)
        if (self.market,self.interval)==('spot','4h'):
            rows=[list(r) for r in self.raw_rows]
            affected=[r for r in rows if r[0]==WARMUP_OPEN]
            if len(affected)!=1 or affected[0][6]!=WARMUP_CLOSE:
                raise ValueError('requires exact approved original H4 warmup closeTime')
            affected[0][6]=CANONICAL_CLOSE  # A derived interval view; raw_rows remain original.
            return validate_rows(rows,'4h',self.coverage_start,self.coverage_end)
        raise ValueError('new source policy only applies to the approved three series')


def verify_five_boundaries(symbol, large, small, start, end, label):
    market='spot' if label.startswith('spot') else 'perp'
    if symbol not in SYMBOLS:
        return verify_join_boundaries(symbol,large,small,start,end,label)
    opens={b.opened_at:b.open for b in small}; closes={b.available_at:b.close for b in small}
    retained=[]
    for bar in large:
        pair=PRICE_DIFFERENCES.get((market,symbol,bar.opened_at.isoformat()))
        mismatch=opens.get(bar.opened_at)!=bar.open or closes.get(bar.available_at)!=bar.close
        if start<=bar.opened_at<end and pair is not None and mismatch:
            op,cp=pair
            actual_open=(bar.open,opens.get(bar.opened_at)); actual_close=(bar.close,closes.get(bar.available_at))
            if ((actual_open!=tuple(map(Decimal,op)) if op else actual_open[0]!=actual_open[1]) or
                    (actual_close!=tuple(map(Decimal,cp)) if cp else actual_close[0]!=actual_close[1])):
                raise ValueError('unapproved added-coin H4/M15 price tuple')
        else: retained.append(bar)
    verify_boundaries(symbol,retained,small,start,end,label)


def stale_symbols(at, symbols):
    stale=[]
    for s in sorted(set(symbols)&SYMBOLS):
        for opened,closed in PARTIAL_CLOSES[s].items():
            finish=int((GAP_OPENS[-1]+timedelta(minutes=15)).timestamp()*1000) if opened==1679661000000 else opened+900000
            if closed+1<=int(at.timestamp()*1000)<finish:
                stale.append(s); break
    return stale


def disclosure():
    return dict(policy=POLICY,original_prices_unchanged=True,synthetic_bars=0,generic_price_tolerance=False,
                original_three_coin_policy=old_disclosure(),
                spot_partial_close_times_ms=PARTIAL_CLOSES,
                spot_missing_opens=[at.isoformat() for at in GAP_OPENS],mark_missing_opens=[GAP_OPEN.isoformat()],
                warmup_interval_view=dict(open_ms=WARMUP_OPEN,original_close_ms=WARMUP_CLOSE,
                    interval_close_ms=CANONICAL_CLOSE,symbols=sorted(SYMBOLS),original_raw_rows_preserved=True),
                price_differences=[dict(market=m,symbol=s,at=at,open_pair=op,close_pair=cp)
                                   for (m,s,at),(op,cp) in PRICE_DIFFERENCES.items()])
