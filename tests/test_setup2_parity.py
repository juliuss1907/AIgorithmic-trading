"""Live Setup-2 evaluator vs research prepare()/simulate() on the same bars (ADR-006)."""
import random
from datetime import timedelta
from decimal import Decimal as D

import pytest

from intraday import setup2
from intraday.replay_v2.contracts import Candle, FundingHistory, FundingSettlement
from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.donchian_filters import entry_filters
from intraday.replay_v2.intraday_timeframes import IntradayCandle
from test_donchian_filter_book import START

DAYS = 12
FIRST = START-setup2.WARMUP_BARS*setup2.H4


def walk(seed):
    """Trending M15 random walk with volume bursts, aggregated into consistent H4 bars."""
    rng, price, small = random.Random(seed), 100.0, []
    for i in range((setup2.WARMUP_BARS+DAYS*6)*16):
        at = FIRST+timedelta(minutes=15*i)
        drift = .0012 if (i//800) % 2 == 0 else -.0012
        close = max(5.0, price*(1+drift+rng.gauss(0, .006)))
        high, low = max(price, close)*(1+abs(rng.gauss(0, .002))), min(price, close)*(1-abs(rng.gauss(0, .002)))
        volume = rng.uniform(5, 15)*(4 if rng.random() < .05 else 1)
        q = lambda v: D(str(round(v, 6)))
        small.append(IntradayCandle(interval='15m', opened_at=at, available_at=at+setup2.M15,
                                    open=q(price), high=q(high), low=q(low), close=q(close), volume=q(volume)))
        price = close
    large = []
    for k in range(0, len(small), 16):
        chunk = small[k:k+16]
        large.append(Candle(opened_at=chunk[0].opened_at, available_at=chunk[-1].available_at,
                            open=chunk[0].open, high=max(c.high for c in chunk), low=min(c.low for c in chunk),
                            close=chunk[-1].close, volume=sum((c.volume for c in chunk), D(0))))
    return tuple(large), tuple(small)


@pytest.fixture(scope='module')
def study():
    cfg = FilterConfig(start=START, end=START+timedelta(days=DAYS), filter_level=0)
    data, funding = {}, {}
    for n, symbol in enumerate(cfg.weights):
        data[symbol] = {}
        for m, market in enumerate(('spot', 'perp')):
            large, small = walk(10*n+m)
            # Research keeps 484h of M15 warmup; the live profile needs only the last 480h.
            data[symbol][market+'4h'] = large
            data[symbol][market+'15m'] = tuple(c for c in small if c.opened_at >= START-timedelta(hours=484))
        data[symbol]['mark15m'] = tuple(c for c in data[symbol]['perp15m'] if c.opened_at >= START)
        funding[symbol] = FundingHistory(symbol=symbol, source='fixture', coverage_start=START, coverage_end=cfg.end,
            settlements=tuple(FundingSettlement(at=START+timedelta(hours=i), rate=0, mark=data[symbol]['mark15m'][0].open)
                              for i in range(0, DAYS*24, 8)))
    prepared = prepare(cfg, data, funding)
    return cfg, data, prepared, simulate(cfg, prepared)


def live_bars(rows, width):
    return setup2.bars([r.row() for r in rows], width)


def test_live_signal_features_profile_and_filters_equal_research(study):
    cfg, data, prepared, _ = study
    checked = entries = 0
    for symbol in cfg.weights:
        for market, side in (('spot', 1), ('perp', -1)):
            h4, m15 = live_bars(data[symbol][market+'4h'], setup2.H4), live_bars(data[symbol][market+'15m'], setup2.M15)
            for at, f in prepared.features[market][symbol].items():
                live = setup2.evaluate_setup2([b for b in h4 if b.available_at <= at],
                                              [b for b in m15 if b.available_at <= at], side=side, anchor=FIRST)
                assert live.bar_close == at and not live.blockers
                for key in ('close', 'volume', 'volume_ma', 'atr', 'ema50', 'prior_ema50', 'ema200',
                            'adx', 'prior_adx', 'plus_di', 'minus_di'):
                    assert live.features[key] == f[key], (symbol, market, at, key)
                assert live.profile == f['profile']
                obs = prepared.observations[market][symbol][at][(30, 10)]
                assert live.donchian_entry == obs['long_entry' if side > 0 else 'short_entry']
                assert live.exit == obs['long_exit' if side > 0 else 'short_exit']
                assert list(live.failed_filters) == entry_filters(f, side, 4, adx_threshold=20)
                assert live.size_multiplier == min(D(1), D('.02')/(f['atr']/f['close']))
                checked += 1
                entries += live.entry
    assert checked == 3*2*DAYS*6


def test_live_trail_equals_every_research_trailing_update(study):
    cfg, data, prepared, report = study
    entries = [e for e in report['events'] if e['kind'] == 'entry_features']
    updates = [e for e in report['events'] if e['kind'] == 'trailing_update']
    assert entries and updates
    compared = 0
    for entry in entries:
        market, symbol = entry['market'], entry['symbol']
        side = 1 if market == 'spot' else -1
        at = setup2.datetime.fromisoformat(entry['at'])
        trade = next(t for t in report['trades'] if t['market'] == market and t['symbol'] == symbol
                     and t['opened_at'] == entry['at'])
        h4 = live_bars(data[symbol][market+'4h'], setup2.H4)
        signal_atr = prepared.features[market][symbol][at]['atr']
        initial = setup2.trail_stop(side, D(trade['entry_price']), signal_atr, [b for b in h4 if b.available_at <= at],
                                    entered_at=at, anchor=FIRST, cap=None)
        assert initial == D(entry['stop'])
        for update in updates:
            when = setup2.datetime.fromisoformat(update['at'])
            if (update['market'], update['symbol']) != (market, symbol) or not at < when <= setup2.datetime.fromisoformat(trade['closed_at']):
                continue
            live = setup2.trail_stop(side, D(trade['entry_price']), signal_atr, [b for b in h4 if b.available_at <= when],
                                     entered_at=at, anchor=FIRST, cap=None)
            assert live == D(update['stop']), (market, symbol, update['at'])
            capped = setup2.trail_stop(side, D(trade['entry_price']), signal_atr, [b for b in h4 if b.available_at <= when],
                                       entered_at=at, anchor=FIRST)
            floor = D(trade['entry_price'])*(1-side*D('.10'))
            assert capped == (max(live, floor) if side > 0 else min(live, floor))
            compared += 1
    assert compared > 0


def test_missing_history_or_profile_blocks_entry_but_not_evaluation():
    h4, m15 = walk(99)
    h4, m15 = live_bars(h4, setup2.H4), live_bars(m15, setup2.M15)
    short = setup2.evaluate_setup2(h4[:599], m15, side=1, anchor=FIRST)
    assert short.blockers == ('history_not_ready',) and not short.entry
    gap = setup2.evaluate_setup2(h4[:650], [b for i, b in enumerate(m15) if i != 9000], side=1, anchor=FIRST)
    assert 'profile_incomplete' in gap.blockers and not gap.entry and gap.bar_close == h4[649].available_at
    unanchored = setup2.evaluate_setup2(h4[1:650], m15, side=1, anchor=FIRST)
    assert unanchored.blockers == ('history_not_ready',)
