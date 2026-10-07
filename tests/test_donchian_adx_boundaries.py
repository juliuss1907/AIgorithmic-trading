from datetime import timedelta
from decimal import Decimal as D

import pytest

from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.intraday_timeframes import IntradayCandle


@pytest.mark.parametrize('symbol', ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])
def test_join_accepts_only_approved_symbol_time_and_open_pair(symbol):
    from intraday.replay_v2.donchian_adx_boundaries import JOIN, OPENS, verify_join_boundaries
    from intraday.replay_v2.donchian_native_boundaries import verify_perp_boundaries
    a,b = OPENS[symbol]
    large = [Candle(opened_at=JOIN, available_at=JOIN+timedelta(hours=4),
                    open=a, close=100, high=100000, low=1, volume=1)]
    small = [IntradayCandle(interval='15m', opened_at=JOIN+timedelta(minutes=15*i),
                           available_at=JOIN+timedelta(minutes=15*(i+1)),
                           open=b, close=100, high=100000, low=1, volume=1) for i in range(16)]
    with pytest.raises(ValueError):
        verify_perp_boundaries(symbol,large,small,JOIN,JOIN+timedelta(hours=4),'old policy')
    verify_join_boundaries(symbol,large,small,JOIN,JOIN+timedelta(hours=4),'join policy')
    for updates in ({'open':a+D('.01')}, {'close':D(101)}):
        with pytest.raises(ValueError):
            verify_join_boundaries(symbol,[large[0].model_copy(update=updates)],small,
                                   JOIN,JOIN+timedelta(hours=4),'unapproved')
    moved = [large[0].model_copy(update={'opened_at':JOIN+timedelta(days=1),
                                        'available_at':JOIN+timedelta(days=1,hours=4)})]
    with pytest.raises(ValueError):
        verify_join_boundaries(symbol,moved,small,JOIN,JOIN+timedelta(days=2),'wrong time')


def test_join_disclosure_retains_old_exceptions_and_no_generic_tolerance():
    from intraday.replay_v2.donchian_adx_boundaries import disclosure, POLICY
    d = disclosure()
    assert d['policy'] == POLICY
    assert len(d['perp_close_differences']) == len(d['perp_open_differences']) == 3
    assert d['original_prices_unchanged'] and not d['generic_price_tolerance']
