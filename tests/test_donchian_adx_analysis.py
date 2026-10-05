from intraday.replay_v2.donchian_adx_analysis import rejected_trades


def test_rejections_separate_lost_winners_from_avoided_losers():
    f=dict(close='110',ema200='100',ema50='105',prior_ema50='104',adx='19',prior_adx='18',
           plus_di='30',minus_di='10',volume='13',volume_ma='10',profile={'vah':109,'val':90})
    trades=[dict(opened_at=at,market='spot',symbol='BTCUSDT',side='long',net_pnl=pnl)
            for at,pnl in [('2022-01-01T00:00:00+00:00','20'),('2022-01-02T00:00:00+00:00','-5')]]
    events=[dict(kind='entry_features',at=t['opened_at'],market=t['market'],symbol=t['symbol'],features=f)
            for t in trades]
    blocked=rejected_trades(trades,events,20)
    assert blocked['closed_trades']==2 and blocked['wins']==blocked['losses']==1
    assert blocked['winning_net_sum']==20 and blocked['losing_net_abs']==5 and blocked['net_pnl']==15
    assert rejected_trades(trades,events,18)['closed_trades']==0
    assert rejected_trades(trades,events,None)['closed_trades']==0
