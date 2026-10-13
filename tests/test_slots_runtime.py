"""Slot design runtime (ADR-007): ranking, free positions, PnL gate, rotation after confirmation, sizing."""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from intraday import active_set
from intraday.contracts import Direction, Regime, SpotRuleParameters
from intraday.execution.allocation import DemoAllocation
from intraday.execution.journal import ExecutionJournal
from intraday.execution.multi_runtime import MultiDemoRuntime
from test_multi_demo_runtime import ACCOUNT, NOW, FakeExchange, Source
from test_setup2_parity import FIRST

COINS = ('ETHUSDT', 'HYPEUSDT', 'NEARUSDT', 'SOLUSDT', 'ZECUSDT')
PROFILE = 'setup2_slots_v1'
RULE = SpotRuleParameters(entry_profile=PROFILE, entry_window=25, exit_window=10)


def features(adx, *, plus=30, minus=10):
    return dict(adx=D(adx), plus_di=D(plus), minus_di=D(minus), volume=D(10), volume_ma=D(5))


def slot_source(world):
    class SlotSource(Source):
        def active_set(self):
            return world['version']

        def rule(self):
            return SimpleNamespace(rule_id=self.symbol+'-'+self.market, parameters=RULE)

        def setup2_signal(self, *, now):
            coin = world['signals'].get(self.symbol, {})
            return SimpleNamespace(entry=coin.get('entry', False), exit=coin.get('exit', False),
                                   size_multiplier=D(coin.get('multiplier', 1)), atr=D(2),
                                   features=features(coin.get('adx', 0), plus=coin.get('plus', 30),
                                                     minus=coin.get('minus', 10))), int(NOW.timestamp()*1000)-1

        def setup2_trail(self, *, side, entry_price, signal_atr, entered_at, now):
            return entry_price*D('.95')

        def latest_decision(self, *, now):
            research, made, rule_id = super().latest_decision(now=now)
            direction = world['jev'].get(self.symbol, Direction.BUY)
            price = float(world['exchange'].mark)   # The evidence tracks the venue, as a fresh live decision would.
            research = SimpleNamespace(features={'mark_price': price, 'reference_price': price})
            return research, made.model_copy(update={'direction': direction}), rule_id
    return SlotSource


def build(tmp_path, *, coins=COINS):
    modes = {s: 'spot' for s in coins}
    plan = active_set.slots_plan(modes, anchors={s: FIRST for s in coins})
    version = active_set.ActiveSetVersion(version_id='v1', seq=1, previous_version_id=None, created_at=NOW,
                                          actor='j', reason='r', plan=plan)
    exchange = FakeExchange()
    world = {'version': version, 'signals': {}, 'jev': {}, 'exchange': exchange}
    exchange.balances.update({s[:-4]: D(0) for s in coins})
    run = MultiDemoRuntime(ExecutionJournal(tmp_path/'execution.sqlite'), ACCOUNT, exchange.venue, slot_source(world),
                           clock=lambda: NOW)
    allocation = DemoAllocation(
        capital=1000, profile=PROFILE, spot_split=D('.5'), perp_split=D('.5'), active_set_version_id='v1',
        spot_emergency_stop_pct=D('.05'),
        spot_weights={s: plan.coins[s].spot_weight for s in coins})
    run.configure(allocation, now=NOW)
    for s in coins:
        run.activate(s, 'spot', evaluation_id=s+'-spot-pass', now=NOW)
    return run, exchange, world


def owned(exchange):
    return {k+'USDT': v for k, v in exchange.balances.items() if k in {c[:-4] for c in COINS} and v}


def results(report):
    return {(r['symbol']): r for r in report['routes']}


def test_five_coins_rank_by_adx_and_the_book_stops_at_three_positions_of_a_third_sleeve(tmp_path):
    run, exchange, world = build(tmp_path)
    world['signals'] = {s: dict(entry=True, adx=a) for s, a in zip(COINS, (22, 41, 35, 28, 50))}
    report = run.cycle(now=NOW)
    assert report['status'] == 'running', report
    taken = owned(exchange)
    assert set(taken) == {'ZECUSDT', 'HYPEUSDT', 'NEARUSDT'}                   # ADX 50, 41, 35
    sleeve = D(1000)*D('.5')
    for quantity in taken.values():
        assert abs(quantity*exchange.mark-sleeve/3) < D('1')                    # a third of the 500 sleeve
    blocked = {s: r for s, r in results(report).items() if r['status'] == 'blocked'}
    assert {s: r['reason'] for s, r in blocked.items()} == {'SOLUSDT': 'position_limit', 'ETHUSDT': 'position_limit'}
    stops = [i for i, u in exchange.orders.values() if i.order_type == 'STOP_LOSS' and not u.terminal]
    assert len(stops) == 3 and all(i.stop_price == exchange.mark*D('.95') for i in stops)


def test_pnl_gate_blocks_new_entries_until_the_open_book_is_in_profit(tmp_path):
    run, exchange, world = build(tmp_path)
    world['signals'] = {'ETHUSDT': dict(entry=True, adx=40)}
    run.cycle(now=NOW)
    assert set(owned(exchange)) == {'ETHUSDT'}
    world['signals'] = {'ETHUSDT': dict(adx=40), 'NEARUSDT': dict(entry=True, adx=45)}
    flat = run.cycle(now=NOW)                       # open book PnL is zero: gate closed
    assert results(flat)['NEARUSDT'] == {'symbol': 'NEARUSDT', 'market': 'spot', 'status': 'blocked',
                                         'reason': 'pnl_gate'}
    assert set(owned(exchange)) == {'ETHUSDT'}
    exchange.mark = D('101')                        # the held position is now in profit
    run.cycle(now=NOW)
    assert set(owned(exchange)) == {'ETHUSDT', 'NEARUSDT'}


def test_rotation_closes_the_weakest_only_for_a_candidate_that_beats_it_by_more_than_five_points(tmp_path):
    run, exchange, world = build(tmp_path)
    world['signals'] = {s: dict(entry=True, adx=a) for s, a in (('ETHUSDT', 20), ('NEARUSDT', 30), ('SOLUSDT', 40))}
    run.cycle(now=NOW)
    assert set(owned(exchange)) == {'ETHUSDT', 'NEARUSDT', 'SOLUSDT'}
    exchange.mark = D('101')
    held_signals = {s: dict(adx=a) for s, a in (('ETHUSDT', 20), ('NEARUSDT', 30), ('SOLUSDT', 40))}
    # +5 exactly is not enough (strictly more than the margin), and the weakest stays.
    world['signals'] = {**held_signals, 'ZECUSDT': dict(entry=True, adx=25)}
    report = run.cycle(now=NOW)
    assert results(report)['ZECUSDT']['reason'] == 'rotation_margin'
    assert set(owned(exchange)) == {'ETHUSDT', 'NEARUSDT', 'SOLUSDT'}
    # Jev vetoes the stronger candidate: nothing is closed.
    world['signals']['ZECUSDT'] = dict(entry=True, adx=26)
    world['jev']['ZECUSDT'] = Direction.SELL
    report = run.cycle(now=NOW)
    assert 'ZECUSDT' not in owned(exchange) and 'ETHUSDT' in owned(exchange)
    assert results(report)['ZECUSDT']['status'] == 'not_authorized'
    # Confirmed and strong enough: the weakest (ETH, ADX 20) is replaced.
    world['jev'].pop('ZECUSDT')
    report = run.cycle(now=NOW)
    assert set(owned(exchange)) == {'NEARUSDT', 'SOLUSDT', 'ZECUSDT'}
    statuses = [r['status'] for r in report['routes']]
    assert 'rotation_exit' in statuses


def test_donchian_exit_frees_the_position_before_new_entries_are_ranked(tmp_path):
    run, exchange, world = build(tmp_path)
    world['signals'] = {s: dict(entry=True, adx=a) for s, a in (('ETHUSDT', 20), ('NEARUSDT', 30), ('SOLUSDT', 40))}
    run.cycle(now=NOW)
    exchange.mark = D('101')
    world['signals'] = {'ETHUSDT': dict(exit=True), 'NEARUSDT': dict(adx=30), 'SOLUSDT': dict(adx=40),
                        'ZECUSDT': dict(entry=True, adx=21)}
    report = run.cycle(now=NOW)
    assert set(owned(exchange)) == {'NEARUSDT', 'SOLUSDT', 'ZECUSDT'}
    assert results(report)['ETHUSDT']['status'] == 'exit'
    assert results(report)['ZECUSDT']['status'] != 'blocked'     # a free position, no rotation margin needed


def test_realized_profit_grows_the_slot_but_unrealized_profit_does_not(tmp_path):
    run, exchange, world = build(tmp_path)
    world['signals'] = {'ETHUSDT': dict(entry=True, adx=30)}
    run.cycle(now=NOW)
    first = owned(exchange)['ETHUSDT']*exchange.mark
    exchange.mark = D('120')                                     # unrealized gain only
    world['signals'] = {'ETHUSDT': dict(adx=30), 'NEARUSDT': dict(entry=True, adx=35)}
    run.cycle(now=NOW)
    near_unrealized = owned(exchange)['NEARUSDT']*exchange.mark
    assert abs(near_unrealized-D(500)/3) < D('2')                # still a third of the original sleeve
    world['signals'] = {'ETHUSDT': dict(exit=True), 'NEARUSDT': dict(adx=35)}
    run.cycle(now=NOW)                                           # realize the gain
    assert 'ETHUSDT' not in owned(exchange)
    exchange.mark = D('121')                                     # the held NEAR is in profit: the gate is open
    world['signals'] = {'NEARUSDT': dict(adx=35), 'SOLUSDT': dict(entry=True, adx=36)}
    run.cycle(now=NOW)
    sol = owned(exchange)['SOLUSDT']*exchange.mark
    assert sol > D(500)/3+D(1) and first > 0                     # the realized gain is reinvested


def test_activation_requires_the_rule_profile_of_the_allocation(tmp_path):
    coins = ('ETHUSDT', 'NEARUSDT')
    run, exchange, world = build(tmp_path, coins=coins)

    class LegacyRule(slot_source(world)):
        def rule(self):
            return SimpleNamespace(rule_id=self.symbol+'-'+self.market,
                                   parameters=SpotRuleParameters(entry_profile='setup2_v1', entry_window=30,
                                                                 exit_window=10))
    run.source_factory = LegacyRule
    with pytest.raises(ValueError, match='only admits Setup-2 champions'):
        run.activate('ETHUSDT', 'spot', evaluation_id='ETHUSDT-spot-pass', now=NOW)
