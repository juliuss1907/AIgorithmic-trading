"""Exact approved Perp source-view differences; never normalize a price."""
from datetime import datetime,timezone
from decimal import Decimal

from intraday.replay_v2.intraday_data import verify_boundaries

POLICY='approved-native-boundary-prices-2023-v1'
OPEN=datetime(2023,11,10,12,tzinfo=timezone.utc)
CLOSES={'BTCUSDT':(Decimal('37118.40'),Decimal('37092.60')),
        'ETHUSDT':(Decimal('2085.32'),Decimal('2091.11')),
        'SOLUSDT':(Decimal('51.1510'),Decimal('50.9140'))}


def verify_perp_boundaries(symbol,large,small,start,end,label):
    opens={b.opened_at:b.open for b in small}; closes={b.available_at:b.close for b in small}
    retained=[]
    for bar in large:
        if (start<=bar.opened_at<end and bar.opened_at==OPEN and symbol in CLOSES and
                (opens.get(bar.opened_at)!=bar.open or closes.get(bar.available_at)!=bar.close)):
            if opens.get(bar.opened_at)!=bar.open or (bar.close,closes.get(bar.available_at))!=CLOSES[symbol]:
                raise ValueError('unapproved Perp boundary price difference')
        else: retained.append(bar)
    verify_boundaries(symbol,retained,small,start,end,label)


def disclosure():
    return dict(policy=POLICY,original_prices_unchanged=True,generic_price_tolerance=False,
        source_views='Native H4 high/low/close for indicators; native M15 prices for fills',
        perp_close_differences={s:dict(at=OPEN.isoformat(),h4_close=str(a),m15_close=str(b)) for s,(a,b) in CLOSES.items()})
