"""Native finer contract bars, separate from the immutable H4 signal contract."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from intraday.replay_v2.contracts import FrozenModel, positive_policy_number, utc


WIDTHS = {'1h': timedelta(hours=1), '30m': timedelta(minutes=30)}


class TrailingCandle(FrozenModel):
    interval: Literal['1h', '30m']
    opened_at: datetime
    available_at: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)

    _times = field_validator('opened_at', 'available_at')(utc)
    _prices = field_validator('open', 'high', 'low', 'close')(positive_policy_number)

    @model_validator(mode='after')
    def valid_bar(self):
        width = WIDTHS[self.interval]
        if self.available_at-self.opened_at != width or self.opened_at.microsecond or (
            int(self.opened_at.timestamp()) % int(width.total_seconds())):
            raise ValueError('expected aligned native trailing candle')
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError('invalid OHLC bounds')
        return self

    @classmethod
    def from_row(cls, row, interval):
        if interval not in WIDTHS or len(row) < 7 or isinstance(row[0], bool) or isinstance(row[6], bool):
            raise ValueError('invalid native trailing candle row')
        width_ms = int(WIDTHS[interval].total_seconds()*1000)
        if row[0] != int(row[0]) or row[0] % width_ms or row[6] != row[0]+width_ms-1:
            raise ValueError('invalid native trailing interval')
        return cls(interval=interval, opened_at=datetime.fromtimestamp(row[0]/1000, timezone.utc),
            available_at=datetime.fromtimestamp((row[6]+1)/1000, timezone.utc),
            open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])

    def row(self):
        opening = int(self.opened_at.timestamp()*1000)
        return [opening, str(self.open), str(self.high), str(self.low), str(self.close), str(self.volume),
                int(self.available_at.timestamp()*1000)-1]
