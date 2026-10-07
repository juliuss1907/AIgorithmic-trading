"""Exactly three additional native Perp open differences at the approved join."""

from datetime import datetime, timezone
from decimal import Decimal

from intraday.replay_v2.donchian_native_boundaries import (
    verify_perp_boundaries, disclosure as original_disclosure,
)


POLICY = 'approved-adx-native-join-prices-2024-v1'
JOIN = datetime(2024, 10, 28, 20, tzinfo=timezone.utc)
OPENS = {'BTCUSDT':(Decimal('69650'), Decimal('69605.80')),
         'ETHUSDT':(Decimal('2518.92'), Decimal('2505.93')),
         'SOLUSDT':(Decimal('176.56'), Decimal('176.35'))}


def verify_join_boundaries(symbol, large, small, start, end, label):
    opens = {b.opened_at:b.open for b in small}
    closes = {b.available_at:b.close for b in small}
    retained = []
    for bar in large:
        mismatch = opens.get(bar.opened_at) != bar.open or closes.get(bar.available_at) != bar.close
        if start <= bar.opened_at < end and bar.opened_at == JOIN and symbol in OPENS and mismatch:
            if ((bar.open, opens.get(bar.opened_at)) != OPENS[symbol] or
                    closes.get(bar.available_at) != bar.close):
                raise ValueError('unapproved ADX join boundary difference')
        else:
            retained.append(bar)
    verify_perp_boundaries(symbol, retained, small, start, end, label)


def disclosure():
    return {**original_disclosure(), 'policy':POLICY,
            'perp_open_differences':{s:dict(at=JOIN.isoformat(), h4_open=str(a), m15_open=str(b))
                                     for s,(a,b) in OPENS.items()}}
