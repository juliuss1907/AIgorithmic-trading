from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Literal

import pytest

from intraday.replay_v2.contracts import Candle, FundingHistory, FundingSettlement
from intraday.replay_v2.donchian_filter_book import ATRTrail
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.donchian_rotation_config import (
    FOUR_COINS, RotationConfig, RotationStressConfig, equal_weights, rotation_cases)
from intraday.replay_v2.intraday_timeframes import IntradayCandle

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
H4 = timedelta(hours=4)


class Loose(RotationConfig):
    """Synthetic candles cannot satisfy ADX/volume filters; the rotation logic is what is under test."""
    filter_level: Literal[0] = 0


def coins(basket):
    return tuple(c+'USDT' for c in basket)


def market_data(basket, *, days=1, jumps=None, boost=None, drift=None, dip=None, side=None):
    """Spot jumps up and Perp jumps down on H4 bar `jumps[coin]` (default -1, so START is the first entry bar).

    `side[coin]`: 'spot' or 'perp' breaks out, the other market stays flat (default: both, which is not a
    market that exists, so the sizing tests pick one). `drift[coin]`: from bar 1 on Spot is 5 higher and Perp
    5 lower (open positions gain). `dip[coin]`: from bar 1 the Spot price falls to this level.
    `boost[coin]`: extra volume on the trigger bar, which raises that coin's rank.
    H4 candles are aggregated from the M15 path, so the two always agree."""
    jumps, boost, drift, dip, side = jumps or {}, boost or {}, drift or {}, dip or {}, side or {}
    data, funding = {}, {}
    end = START+timedelta(days=days)
    for symbol in coins(basket):
        coin = symbol.removesuffix('USDT')
        data[symbol] = {}
        jump = jumps.get(coin, -1)
        for market, up in (('spot', 1), ('perp', -1)):
            def level(j):
                bar = j//16
                if side.get(coin, market) != market:
                    return D(100)
                if coin in dip and bar >= 1 and market == 'spot':
                    return D(dip[coin])
                price = D(100)+up*10 if bar >= jump else D(100)
                if coin in drift and bar >= 1:
                    price += up*5
                return price
            small = []
            for j in range(-1936, days*96):
                at = START+timedelta(minutes=15*j)
                p = level(j)
                extra = D(boost.get(coin, 0))/16 if j//16 == jump-1 or (jump == -1 and j//16 == -1) else D(0)
                small.append(IntradayCandle(interval='15m', opened_at=at, available_at=at+timedelta(minutes=15),
                                            open=p, high=p+1, low=p-1, close=p, volume=D(10)+extra))
            data[symbol][market+'15m'] = tuple(small)
            large = []
            for i in range(-600, days*6):
                chunk = [c for c in small if START+H4*i <= c.opened_at < START+H4*(i+1)]
                if not chunk:  # The H4 warmup reaches further back than the M15 history.
                    q = level(i*16)
                    large.append(Candle(opened_at=START+H4*i, available_at=START+H4*(i+1), open=q, high=q+1,
                                        low=q-1, close=q, volume=D(160)))
                    continue
                large.append(Candle(opened_at=START+H4*i, available_at=START+H4*(i+1), open=chunk[0].open,
                                    high=max(c.high for c in chunk), low=min(c.low for c in chunk),
                                    close=chunk[-1].close, volume=sum((c.volume for c in chunk), D(0))))
            data[symbol][market+'4h'] = tuple(large)
        data[symbol]['mark15m'] = tuple(c for c in data[symbol]['perp15m'] if c.opened_at >= START)
        funding[symbol] = FundingHistory(symbol=symbol, source='fixture', coverage_start=START, coverage_end=end,
            settlements=tuple(FundingSettlement(at=START+timedelta(hours=h), rate=0, mark=90)
                              for h in range(0, days*24, 8)))
    return data, funding


def run(basket=FOUR_COINS, *, days=1, inject=None, cls=Loose, change=None, **scenario):
    data, funding = market_data(basket, days=days, **scenario)
    weights = equal_weights(basket)
    config = cls(start=START, end=START+timedelta(days=days), basket=basket, weights=weights,
                 perp_weights=dict(weights), **(change or {}))
    prepared = prepare(config, data, funding)
    for (market, symbol, at), values in (inject or {}).items():
        prepared.features[market][symbol+'USDT'][at].update(values)
    return config, simulate(config, prepared)


def events(report, kind, reason=None):
    return [e for e in report['events'] if e['kind'] == kind and (reason is None or e['reason'] == reason)]


def test_hard_stop_is_the_tighter_of_the_percentage_and_the_atr_stop_and_never_loosens():
    long = ATRTrail(1, D(100), D(4), 3, D('.05'))
    short = ATRTrail(-1, D(100), D(4), 3, D('.05'))
    assert long.stop == D(95) and short.stop == D(105)       # ATR stop 88/112 is farther than 5%
    wide = ATRTrail(1, D(100), D('0.5'), 3, D('.05'))
    assert wide.stop == D('98.5')                            # ATR stop 98.5 is already tighter than 5%
    long.update(D(120), D(110), D(4))
    assert long.stop == D(108)                               # 120-12 ratchets above the hard stop
    long.update(D(100), D(90), D(40))
    assert long.stop == D(108)                               # and never loosens
    assert ATRTrail(1, D(100), D(4), 3).stop == D(88) and ATRTrail(1, D(100), D(4), 3).hard is None


def test_cases_cover_the_registered_family_and_the_config_is_closed():
    cases = rotation_cases(START, START+timedelta(days=30))
    names = [n for n, _ in cases]
    assert len(names) == 18 and len(set(names)) == 18
    assert names[:2] == ['ETH-NEAR-SOL-H4', 'ETH-NEAR-SOL-H4-cost2x']
    assert {c.max_positions for n, c in cases if 'ZEC' in n} == {3, 4}
    assert all(c.entry_cap == D('.5') and c.perp_cap == D('.5') for _, c in cases if hasattr(c, 'max_positions'))
    w = equal_weights(('ETH', 'NEAR', 'SOL'))
    base = dict(start=START, end=START+timedelta(days=1), weights=w, perp_weights=dict(w))
    with pytest.raises(ValueError):
        RotationConfig(basket=('ETH', 'NEAR', 'SOL'), hard_stop_pct=D('.07'), **base)
    with pytest.raises(ValueError):
        RotationConfig(basket=('ETH', 'NEAR', 'SOL'), max_positions=4, **base)     # More positions than coins
    with pytest.raises(ValueError):
        RotationConfig(basket=('SOL', 'NEAR', 'ETH'), **base)                      # Unsorted
    assert RotationStressConfig(basket=('ETH', 'NEAR', 'SOL'), **base).cost_multiplier == 2


def test_uncapped_position_uses_the_sleeve_and_the_slot_variant_a_third_of_it():
    basket = ('ETH', 'NEAR', 'SOL')
    side = {'ETH': 'spot', 'NEAR': 'spot', 'SOL': 'perp'}
    _, uncapped = run(basket, side=side, change=dict(pnl_gate=False))
    _, slot = run(basket, side=side, change=dict(pnl_gate=False, position_cap='slot'))
    spot_u = [e for e in events(uncapped, 'entry') if e['market'] == 'spot']
    spot_s = [e for e in events(slot, 'entry') if e['market'] == 'spot']
    assert len(spot_u) == 2 and len(spot_s) == 2
    mult = {e['symbol']: D(e['atr_size_multiplier']) for e in events(uncapped, 'entry_features')}
    sleeve = D(500)
    # Spot request = sleeve (or a third of it) times the ATR multiplier; the uncapped first entry takes it all.
    first = spot_u[0]['symbol']
    assert abs(D(spot_u[0]['requested_notional'])-sleeve*mult[first]) < D('1e-9')
    assert abs(D(spot_s[0]['requested_notional'])-sleeve/3*mult[first]) < D('1e-9')
    assert abs(D(spot_u[0]['notional'])-sleeve*mult[first]) < D('1e-9')
    assert D(spot_u[1]['notional']) < sleeve*mult[spot_u[1]['symbol']]   # Only the free principal is left.
    assert abs(D(spot_s[1]['notional'])/(sleeve/3*mult[spot_s[1]['symbol']])-1) < D('.001')  # Less entry costs
    perp = [e for e in events(uncapped, 'entry') if e['market'] == 'perp']
    assert len(perp) == 1 and D(perp[0]['requested_notional']) == sleeve  # Own sleeve: Spot did not touch it.


def test_a_full_book_blocks_the_fourth_coin_when_nothing_can_be_rotated_out():
    side = {'ETH': 'spot', 'NEAR': 'spot', 'SOL': 'perp', 'ZEC': 'perp'}
    _, report = run(side=side, change=dict(pnl_gate=False))
    entered = {e['symbol'] for e in events(report, 'entry')}
    assert entered == {'ETHUSDT', 'NEARUSDT', 'SOLUSDT'}
    blocked = events(report, 'entry_blocked', 'position_limit')
    assert [e['symbol'] for e in blocked] == ['ZECUSDT']
    assert len(events(report, 'rotation')) == 0


def ranked_inject(at, **adx):
    """Set ADX for both markets; DMI points the way of each market's position."""
    inject = {}
    for coin, value in adx.items():
        inject[('spot', coin, at)] = dict(adx=D(value), prior_adx=D(value)-1, plus_di=D(30), minus_di=D(10))
        inject[('perp', coin, at)] = dict(adx=D(value), prior_adx=D(value)-1, plus_di=D(10), minus_di=D(30))
    return inject


LATE = START+H4*3  # ZEC's breakout bar (index 2) closes here.
SIDE = {'ETH': 'spot', 'NEAR': 'spot', 'SOL': 'perp', 'ZEC': 'perp'}


def test_late_stronger_signal_rotates_out_the_weakest_open_position():
    _, report = run(jumps={'ZEC': 2}, side=SIDE, days=2, change=dict(pnl_gate=False),
                    inject=ranked_inject(LATE, ETH=10, NEAR=25, SOL=30, ZEC=40))
    rotation = events(report, 'rotation')
    assert len(rotation) == 1
    assert (rotation[0]['symbol'], rotation[0]['closed_symbol']) == ('ZECUSDT', 'ETHUSDT')
    out = [t for t in report['trades'] if t['exit_reason'] == 'rotation_exit']
    assert [t['symbol'] for t in out] == ['ETHUSDT'] and out[0]['closed_at'] == LATE.isoformat()
    assert any(e['symbol'] == 'ZECUSDT' and e['at'] == LATE.isoformat() for e in events(report, 'entry'))
    selection = events(report, 'selection')[-1]
    assert any(d['action'] == 'rotated' for d in selection['decisions'])


def test_rotation_needs_the_margin_and_can_be_switched_off():
    _, close = run(jumps={'ZEC': 2}, side=SIDE, days=2, change=dict(pnl_gate=False),
                   inject=ranked_inject(LATE, ETH=10, NEAR=25, SOL=30, ZEC=14))
    assert not events(close, 'rotation')
    assert events(close, 'entry_blocked', 'rotation_margin')
    _, off = run(jumps={'ZEC': 2}, side=SIDE, days=2, change=dict(pnl_gate=False, rotation='off'),
                 inject=ranked_inject(LATE, ETH=10, NEAR=25, SOL=30, ZEC=40))
    assert not events(off, 'rotation')
    assert any(e['symbol'] == 'ZECUSDT' for e in events(off, 'entry_blocked', 'position_limit'))


def test_pnl_gate_blocks_new_entries_while_the_open_book_is_flat_or_losing_and_opens_when_in_profit():
    inject = ranked_inject(LATE, ETH=10, NEAR=25, SOL=30, ZEC=40)
    _, flat = run(jumps={'ZEC': 2}, side=SIDE, days=2, inject=inject)
    gate = events(flat, 'entry_blocked', 'pnl_gate')
    assert gate and all(D(e['open_book_pnl']) <= 0 for e in gate) and not events(flat, 'rotation')
    _, winning = run(jumps={'ZEC': 2}, side=SIDE, days=2, inject=inject, drift={'ETH': 1, 'NEAR': 1, 'SOL': 1})
    assert events(winning, 'rotation') and not events(winning, 'entry_blocked', 'pnl_gate')
    # The first bar has nothing open, so the gate never blocks it.
    assert len(events(winning, 'entry')) >= 3


def test_fixed_five_percent_stop_labels_and_caps_the_exit():
    kw = dict(basket=('ETH', 'NEAR', 'SOL'), side={'ETH': 'spot', 'NEAR': 'perp', 'SOL': 'perp'}, dip={'ETH': 105})
    _, hard = run(change=dict(pnl_gate=False), **kw)
    _, soft = run(change=dict(pnl_gate=False, hard_stop_pct=None), **kw)
    # Spot long from 110: the ratcheted ATR stop sits near 103.5, the 5% stop at 104.5; a bar trading 104-106 only touches the latter.
    eth_hard = next(t for t in hard['trades'] if t['symbol'] == 'ETHUSDT')
    eth_soft = next(t for t in soft['trades'] if t['symbol'] == 'ETHUSDT')
    assert eth_hard['exit_reason'] == 'hard_stop_touch' and D(eth_hard['net_pnl']) < 0
    assert eth_soft['exit_reason'] == 'window_end'


def test_runs_are_deterministic():
    kw = dict(jumps={'ZEC': 2}, side=SIDE, days=2, change=dict(pnl_gate=False),
              inject=ranked_inject(LATE, ETH=10, NEAR=25, SOL=30, ZEC=40))
    first, second = run(**kw)[1], run(**kw)[1]
    assert first['events'] == second['events'] and first['trades'] == second['trades']


def _item(tmp_path, name, ret, dd, trades=10, spot=100.0, perp=50.0, early=40.0, events=(), final=1150.0):
    import json
    directory = tmp_path/name
    directory.mkdir()
    (directory/'equity_curve.jsonl').write_text('\n'.join(json.dumps(r) for r in (
        dict(at='2022-01-02T00:00:00+00:00', equity_known='1000'),
        dict(at='2024-10-28T00:00:00+00:00', equity_known=str(1000+early)),
        dict(at='2026-10-01T00:00:00+00:00', equity_known=str(final)))))
    (directory/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
    (directory/'trades.jsonl').write_text('')
    return dict(variant=name, report_directory=str(directory), summary=dict(
        net_return_pct=ret, max_drawdown_known_pct=dd, closed_trades=trades, daily_stops=0, exit_reasons={},
        initial_capital=1000, final_equity_known=final,
        contributions={'spot:ETHUSDT': dict(net_pnl=spot), 'perp:ETHUSDT': dict(net_pnl=perp)}))


def test_verdict_applies_the_five_criteria_against_the_named_control(tmp_path):
    from intraday.replay_v2.donchian_rotation_verdict import evaluate_rotation, mechanism_profile
    start, end = '2022-01-01T00:00:00+00:00', '2026-10-02T00:00:00+00:00'
    events = [dict(kind='rotation', reason='close_weakest_for_new_signal'),
              dict(kind='entry_blocked', reason='pnl_gate'), dict(kind='entry_blocked', reason='pnl_gate'),
              dict(kind='entry_blocked', reason='indicator_filters')]
    names = {
        'ETH-NEAR-SOL-H4': _item(tmp_path, 'h4', 81.7, 11.6), 'ETH-NEAR-SOL-FIXED5050': _item(tmp_path, 'fx', 60.0, 10.0),
        'ETH-NEAR-SOL-FIXED5050-cost2x': _item(tmp_path, 'fxc', 40.0, 10.0),
        'ETH-NEAR-SOL-ROT': _item(tmp_path, 'rot', 70.0, 9.0, events=events),
        'ETH-NEAR-SOL-ROT-cost2x': _item(tmp_path, 'rotc', 50.0, 9.0),
    }
    for name, item in names.items():
        item['variant'] = name
    receipt = dict(window=dict(start=start, end=end), results=list(names.values()))
    verdict = evaluate_rotation(receipt)
    entry = verdict['candidates']['ETH-NEAR-SOL-ROT']
    assert entry['advance'] and [c['name'] for c in entry['criteria']][0] == 'net_return_not_below_control'
    assert entry['criteria'][0]['control_case'] == 'ETH-NEAR-SOL-FIXED5050'
    assert entry['mechanisms'] == dict(rotations=1, pnl_gate=2)
    names['ETH-NEAR-SOL-ROT']['summary']['max_drawdown_known_pct'] = 16.0
    failed = evaluate_rotation(dict(window=receipt['window'], results=list(names.values())))
    assert not failed['candidates']['ETH-NEAR-SOL-ROT']['advance'] and failed['advancing'] == []
    assert mechanism_profile(names['ETH-NEAR-SOL-ROT']['report_directory'])['rotations'] == 1
