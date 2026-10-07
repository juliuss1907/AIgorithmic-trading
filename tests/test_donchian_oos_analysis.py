from datetime import datetime, timedelta, timezone
from decimal import Decimal

from intraday.replay_v2.donchian_oos_analysis import bootstrap, annual_equity, trade_stats, buy_hold, daily_returns


def test_bootstrap_is_seeded_and_preserves_constant_returns():
    daily=[.01]*30
    first=bootstrap(daily,draws=100,seed=20261005)
    assert first==bootstrap(daily,draws=100,seed=20261005)
    expected=((1.01**30)-1)*100
    for block in first.values():
        assert abs(block['return_pct_95_interval'][0]-expected)<1e-9


def test_annual_marked_change_is_not_realized_trade_pnl():
    curve=[{'at':'2022-12-31T23:59:59+00:00','equity_known':'1100'},
           {'at':'2023-12-31T23:59:59+00:00','equity_known':'1210'}]
    result=annual_equity(curve,1000)
    assert result['2022']['marked_change_usdt']==100
    assert result['2023']['marked_change_usdt']==110
    assert result['2023']['return_pct']==10


def test_trade_stats_reconcile_and_buy_hold_charges_both_fills():
    assert trade_stats([{'net_pnl':'2'},{'net_pnl':'-1'}])['net_pnl']==1
    from types import SimpleNamespace
    at=datetime(2022,1,1,tzinfo=timezone.utc)
    cfg=SimpleNamespace(start=at,end=at+timedelta(days=1),capital=Decimal(1000),
                        weights={'BTCUSDT':Decimal(1)})
    from intraday.replay_v2.intraday_timeframes import IntradayCandle
    row=IntradayCandle(interval='15m',opened_at=at,available_at=at+timedelta(minutes=15),
                       open=100,high=100,low=100,close=100,volume=10)
    result=buy_hold(cfg,{'BTCUSDT':{'spot15m':[row]}})
    expected=1000/1.0015*(1-.0015)
    assert abs(result['final_usdt']-expected)<1e-9


def test_bootstrap_excludes_partial_day_and_midnight_terminal_point():
    from types import SimpleNamespace
    start=datetime(2022,1,1,tzinfo=timezone.utc)
    rows=[{'at':'2022-01-01T23:59:59+00:00','equity_known':'1100'},
          {'at':'2022-01-02T23:59:59+00:00','equity_known':'1210'},
          {'at':'2022-01-03T00:00:00+00:00','equity_known':'1210'}]
    config=SimpleNamespace(start=start,end=start+timedelta(days=2),capital=1000)
    assert len(daily_returns(rows,config))==2
    config.end+=timedelta(hours=20)
    rows[-1]={'at':'2022-01-03T19:59:59+00:00','equity_known':'1400'}
    assert len(daily_returns(rows,config))==2
