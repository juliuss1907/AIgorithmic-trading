from datetime import timedelta
from decimal import Decimal as D

from test_donchian_filter_book import START
from intraday.replay_v2.contracts import Candle, FundingHistory, FundingSettlement
from intraday.replay_v2.intraday_timeframes import IntradayCandle
from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.metrics import fingerprint


def fixture(days=1):
    cfg = FilterConfig(start=START, end=START+timedelta(days=days))
    data, funding = {}, {}
    for symbol in cfg.weights:
        data[symbol] = {}
        for market, price in [('spot',D(110)), ('perp',D(90))]:
            h4 = []
            for i in range(-600, days*6):
                at = START+timedelta(hours=i*4)
                p = price if i >= -1 else D(100)
                h4.append(Candle(opened_at=at, available_at=at+timedelta(hours=4),
                                open=p, high=p+1, low=p-1, close=p, volume=160))
            data[symbol][market+'4h'] = tuple(h4)
            small = []
            for i in range(-1936, days*96):
                at = START+timedelta(minutes=i*15)
                p = price if i >= -16 else D(100)
                small.append(IntradayCandle(interval='15m', opened_at=at,
                    available_at=at+timedelta(minutes=15), open=p, high=p+1, low=p-1, close=p, volume=10))
            data[symbol][market+'15m'] = tuple(small)
        data[symbol]['mark15m'] = tuple(c for c in data[symbol]['perp15m'] if c.opened_at >= START)
        funding[symbol] = FundingHistory(symbol=symbol, source='fixture', coverage_start=START,
            coverage_end=cfg.end, settlements=tuple(FundingSettlement(at=START+timedelta(hours=i),
                rate=0, mark=90) for i in range(0, days*24, 8)))
    return cfg, data, funding


def test_both_sleeves_enter_and_flat_end_reconciles_with_replay():
    cfg, data, funding = fixture()
    p = prepare(cfg, data, funding)
    report = simulate(cfg, p)
    assert len(report['trades']) == 6
    assert {t['side'] for t in report['trades']} == {'long','short'}
    assert all(t['closed_at'] == cfg.end.isoformat() for t in report['trades'])
    assert report['summary']['realized_sizing']['final_capital']['unallocated'] == 0
    assert report['summary']['pnl_after_known_costs'] < 0
    assert fingerprint(report) == fingerprint(simulate(cfg, p))


def test_parent_loss_at_m15_close_flattens_both_next_open_and_cancels_entries():
    cfg, data, funding = fixture(days=2)
    for s in data:
        rows = list(data[s]['mark15m'])
        rows[1] = rows[1].model_copy(update={'close':D(130), 'high':D(130)})
        data[s]['mark15m'] = tuple(rows)
    report = simulate(cfg, prepare(cfg,data,funding))
    halts = [e for e in report['events'] if e['kind'] == 'halt']
    assert halts[0]['at'] == (START+timedelta(minutes=30)-timedelta(milliseconds=1)).isoformat()
    assert halts[0]['reason'] == 'daily_loss_limit'
    exits = [t for t in report['trades'] if t['exit_reason'] == 'daily_loss_limit']
    assert len(exits) == 6
    assert {t['closed_at'] for t in exits} == {(START+timedelta(minutes=30)).isoformat()}
    assert any(e['kind'] == 'resume' and e['at'].startswith('2024-10-30') for e in report['events'])


def test_stop_gap_uses_unfavorable_open_not_old_stop_and_no_same_bar_reentry():
    cfg, data, funding = fixture()
    rows = list(data['BTCUSDT']['spot15m'])
    i = next(i for i,b in enumerate(rows) if b.opened_at == START+timedelta(minutes=15))
    rows[i] = rows[i].model_copy(update={'open':D(100), 'low':D(99), 'close':D(105)})
    data['BTCUSDT']['spot15m'] = tuple(rows)
    report = simulate(cfg, prepare(cfg,data,funding))
    trade = next(t for t in report['trades'] if t['market']=='spot' and t['symbol']=='BTCUSDT')
    assert D(trade['exit_price']) == 100
    assert trade['exit_reason'] == 'atr_stop_gap'


def test_profile_excludes_current_h4_and_future_volume_cannot_change_prior_signal():
    cfg, data, funding = fixture()
    before = prepare(cfg,data,funding)
    rows = list(data['BTCUSDT']['spot15m'])
    i = next(i for i,b in enumerate(rows) if b.opened_at == START)
    rows[i] = rows[i].model_copy(update={'volume':D(999999)})
    data['BTCUSDT']['spot15m'] = tuple(rows)
    after = prepare(cfg,data,funding)
    assert before.features['spot']['BTCUSDT'][START] == after.features['spot']['BTCUSDT'][START]


def test_new_h4_trail_never_fills_retroactively_and_gap_is_checked_immediately():
    cfg,data,funding = fixture()
    large = list(data['BTCUSDT']['spot4h'])
    large[600] = large[600].model_copy(update={'high':D(120)})
    data['BTCUSDT']['spot4h'] = tuple(large)
    small = list(data['BTCUSDT']['spot15m'])
    index = next(i for i,b in enumerate(small) if b.opened_at == START+timedelta(hours=3,minutes=45))
    small[index] = small[index].model_copy(update={'high':D(120)})
    data['BTCUSDT']['spot15m'] = tuple(small)
    report = simulate(cfg,prepare(cfg,data,funding))
    trade = next(t for t in report['trades'] if t['market']=='spot' and t['symbol']=='BTCUSDT')
    assert trade['closed_at'] == (START+timedelta(hours=4)).isoformat()
    assert trade['exit_reason'] == 'atr_stop_gap'
    assert D(trade['exit_price']) == 110
    assert len([e for e in report['events'] if e['kind']=='entry' and e['market']=='spot' and e['symbol']=='BTCUSDT']) == 1
