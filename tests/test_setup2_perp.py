"""Stage C: rule-driven Setup-2 Perp short (profile on perp_intraday, no schema rebuild)."""
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from intraday import active_set, setup2, setup2_store
from intraday.contracts import (DecisionScope, Direction, JevDecision, PerpRuleParameters, PerpRuleProposal,
                                Regime, RiskLevel, ScopedRuleCandidate)
from intraday.execution.allocation import DemoAllocation
from intraday.execution.journal import ExecutionJournal
from intraday.execution.multi_runtime import MultiDemoRuntime
from intraday.portfolio_coordinator import ParentPortfolioCoordinator, ParentPortfolioPolicy, ParentPortfolioState
from intraday.replay_v2.contracts import FundingHistory, FundingSettlement, ReplayConfig, ReplayDataset, binance_gate_profile
from intraday.replay_v2.engine import simulate
from intraday.replay_v2.gates import GateEvidence, evaluate_report_gate
from intraday.scoped_gate import ScopedEntryGate
from intraday.scoped_rule_lifecycle import rule_allows_answers
from intraday.setup2_cli import candidate_rule
from intraday.store import IntradayStore
from test_multi_demo_runtime import ACCOUNT, NOW, FakeExchange, Source
from test_setup2_parity import FIRST, walk

SETUP2 = PerpRuleParameters(entry_profile='setup2_v1', allowed_regimes=(Regime.TRENDING_DOWN, Regime.SIDEWAYS))


def bearish(seed=29):
    """Price-inverted random walk: its long setups become short setups (seed 29 has four)."""
    from intraday.replay_v2.contracts import Candle
    k, q = D(10000), D('0.000001')

    def invert(rows):
        return [[r[0], str((k/D(r[1])).quantize(q)), str((k/D(r[3])).quantize(q)), str((k/D(r[2])).quantize(q)),
                 str((k/D(r[4])).quantize(q)), r[5], r[6]] for r in rows]
    large, small = walk(seed)
    large_rows, small_rows = invert([c.row() for c in large]), invert([c.row() for c in small])
    return tuple(Candle.from_row(r) for r in large_rows), large_rows, small_rows


def decision(direction, confidence=.9):
    return JevDecision(decision_id='d', tick_id='t', snapshot_id='s', direction=direction,
                       direction_confidence=confidence, regime=Regime.TRENDING_DOWN, toxic_flow=.1, entry_quality=4,
                       risk_level=RiskLevel.LOW, model_ref='fixture', created_at=NOW)


def answers(direction):
    return {'direction': {'choice': direction.value, 'probabilities': {direction.value: .9}},
            'regime': {'choice': Regime.TRENDING_DOWN.value}, 'risk_level': {'choice': RiskLevel.LOW.value},
            'toxic_flow': {'noul': .1}, 'entry_quality': {'score': 3}}


def test_perp_profile_is_hidden_by_default_and_never_model_selected():
    assert 'entry_profile' not in PerpRuleParameters().model_dump(mode='json')
    assert PerpRuleParameters.model_validate_json(SETUP2.model_dump_json()) == SETUP2
    with pytest.raises(ValueError, match='legacy Jev intraday'):
        PerpRuleProposal(parameters=SETUP2, rationale='a model must not select the Setup-2 short')
    rule = SimpleNamespace(scope=DecisionScope.PERP_INTRADAY, parameters=SETUP2)
    assert rule_allows_answers(rule, answers(Direction.SELL))
    assert not rule_allows_answers(rule, answers(Direction.BUY))
    legacy = SimpleNamespace(scope=DecisionScope.PERP_INTRADAY, parameters=PerpRuleParameters())
    assert rule_allows_answers(legacy, answers(Direction.BUY))


def test_setup2_short_gate_requires_rule_setup_and_jev_sell():
    policy = ParentPortfolioPolicy.from_split(D('.6'), D('.4'), 1)
    gate = ScopedEntryGate(ParentPortfolioCoordinator(policy))
    state = ParentPortfolioState(mark_price=100, perp_mark_price=100, spot_price=100, day_start_equity=10_000,
                                 high_water_mark=10_000, entries_paused=False, paper_active=True, updated_at=NOW)
    kw = dict(size_multiplier=.5, projected_isolated_margin_pct=.1)
    assert gate.perp_setup2_entry(state, decision(Direction.SELL), SETUP2, setup_entry=False, **kw).reason_codes == (
        'no_setup2_short_setup',)
    for direction in (Direction.BUY, Direction.HOLD, Direction.TAKE_PROFIT):
        denied = gate.perp_setup2_entry(state, decision(direction), SETUP2, setup_entry=True, **kw)
        assert not denied.allowed and 'setup2_requires_jev_sell' in denied.reason_codes
    allowed = gate.perp_setup2_entry(state, decision(Direction.STRONG_SELL), SETUP2, setup_entry=True, **kw)
    assert allowed.allowed and allowed.target_notional == pytest.approx(-10_000*.4*.5)


def test_setup2_short_replay_trails_down_settles_funding_and_gates(tmp_path):
    large, _, small_rows = bearish()
    rule = ScopedRuleCandidate.create(rule_id='eth-perp-setup2', parent_rule_id='bootstrap', thesis_id='adr-006',
        symbol='ETHUSDT', scope=DecisionScope.PERP_INTRADAY, parameters=SETUP2, created_at=FIRST,
        model_ref='operator/test', prompt_version='setup2-v1')
    start, end = FIRST+600*setup2.H4, large[-1].available_at
    funding = FundingHistory(symbol='ETHUSDT', source='fixture', coverage_start=start, coverage_end=end,
        settlements=tuple(FundingSettlement(at=start+timedelta(hours=h), rate=D('.0001'), mark=large[600].open)
                          for h in range(0, int((end-start).total_seconds()//3600), 8)))
    config = ReplayConfig(symbol='ETHUSDT', market='perp', rule_id=rule.rule_id, start=start, end=end, capital=1000,
                          leverage=1, profile=binance_gate_profile(funding=funding))
    data = ReplayDataset(rule=rule, candles=large, profile_candles=tuple(setup2.bars(small_rows, setup2.M15)),
                         indicator_anchor=FIRST)
    report = simulate(config, data)
    entries = [e for e in report['events'] if e['kind'] == 'entry']
    assert entries and all(e['side'] == -1 and D(e['price']) < D(e['stop']) <= D(e['price'])*D('1.1') for e in entries)
    assert all(t['side'] == 'short' for t in report['trades'])
    assert any(e['kind'] == 'funding' for e in report['events'])
    assert {'setup2_perp_marked_at_trade_prices', 'setup2_bar_level_funding'} <= set(report['limitations'])
    outcome = evaluate_report_gate(report, GateEvidence(history_bars=len(large), history_coverage=1,
                                                        latest_candle_age_seconds=60))
    assert 'funding_coverage_incomplete' not in outcome.reason_codes and outcome.metrics['hard_risk_violations'] == 0
    with pytest.raises(ValueError, match='Isolated 1x'):
        simulate(config.model_copy(update={'leverage': 3}), data)


def perp_source(current):
    class PerpSource(Source):
        def active_set(self):
            return current['version']

        def rule(self):
            return SimpleNamespace(rule_id=self.symbol+'-'+self.market, parameters=SETUP2)

        def setup2_signal(self, *, now):
            return SimpleNamespace(entry=current.get('entry', True), exit=False, size_multiplier=D(1),
                                   atr=D(2)), int(NOW.timestamp()*1000)-1

        def setup2_trail(self, *, side, entry_price, signal_atr, entered_at, now):
            return entry_price*current['trail_ratio']

        def latest_decision(self, *, now):
            research, made, rule_id = super().latest_decision(now=now)
            return research, made.model_copy(update={'direction': current['direction'],
                                                      'regime': Regime.TRENDING_DOWN}), rule_id
    return PerpSource


def test_demo_setup2_short_enters_on_jev_sell_and_moves_stops_place_first(tmp_path):
    coin = active_set.CoinPlan(mode='perp', spot_weight=D(0), perp_weight=D(1), indicator_anchor=FIRST)
    version = active_set.ActiveSetVersion(version_id='v1', seq=1, previous_version_id=None, created_at=NOW,
                                          actor='j', reason='r', plan=active_set.ActiveSetPlan(coins={'ETHUSDT': coin}))
    current = {'version': version, 'direction': Direction.BUY, 'trail_ratio': D('1.05')}
    exchange = FakeExchange()
    exchange.leverage = 1
    journal = ExecutionJournal(tmp_path/'execution.sqlite')
    journal.save_settings_request({'id': 's', 'account': ACCOUNT.key, 'symbol': 'ETHUSDT', 'status': 'verified'},
                                  now=NOW, verified_leverage=1)
    run = MultiDemoRuntime(journal, ACCOUNT, exchange.venue, perp_source(current), clock=lambda: NOW)
    run.configure(DemoAllocation(capital=1000, perp_weights={'ETHUSDT': D(1)}, profile='setup2_v1',
                                 spot_split=D('.6'), perp_split=D('.4'), active_set_version_id='v1'), now=NOW)
    run.activate('ETHUSDT', 'perp', evaluation_id='ETHUSDT-perp-pass', now=NOW)
    run.cycle(now=NOW)
    assert not exchange.positions.get('ETHUSDT')  # Jev BUY never confirms a rule short.
    current['direction'] = Direction.SELL
    assert run.cycle(now=NOW)['status'] == 'running'
    assert exchange.positions['ETHUSDT'] < 0
    stops = [i for i, u in exchange.orders.values() if i.order_type == 'STOP_MARKET' and not u.terminal]
    assert len(stops) == 1 and stops[0].side == 'BUY' and stops[0].stop_price == D('105.00')
    current['trail_ratio'] = D('1.02')  # Tighter trail: the new stop is placed before the old is cancelled.
    run.cycle(now=NOW)
    live = [i for i, u in exchange.orders.values() if i.order_type == 'STOP_MARKET' and not u.terminal]
    assert [i.stop_price for i in live] == [D('102.00')]
    order = list(exchange.orders)
    old, new = stops[0].intent_id, live[0].intent_id
    assert order.index(new) > order.index(old)
    current['trail_ratio'] = D('1.08')  # A looser trail never moves a short stop up.
    run.cycle(now=NOW)
    assert [i.stop_price for i, u in exchange.orders.values() if i.order_type == 'STOP_MARKET' and not u.terminal] == [D('102.00')]


def test_setup2_perp_requires_isolated_1x(tmp_path):
    coin = active_set.CoinPlan(mode='perp', spot_weight=D(0), perp_weight=D(1), indicator_anchor=FIRST)
    version = active_set.ActiveSetVersion(version_id='v1', seq=1, previous_version_id=None, created_at=NOW,
                                          actor='j', reason='r', plan=active_set.ActiveSetPlan(coins={'ETHUSDT': coin}))
    current = {'version': version, 'direction': Direction.SELL, 'trail_ratio': D('1.05')}
    exchange = FakeExchange()
    journal = ExecutionJournal(tmp_path/'execution.sqlite')
    journal.save_settings_request({'id': 's', 'account': ACCOUNT.key, 'symbol': 'ETHUSDT', 'status': 'verified'},
                                  now=NOW, verified_leverage=3)
    run = MultiDemoRuntime(journal, ACCOUNT, exchange.venue, perp_source(current), clock=lambda: NOW)
    run.configure(DemoAllocation(capital=1000, perp_weights={'ETHUSDT': D(1)}, profile='setup2_v1',
                                 spot_split=D('.6'), perp_split=D('.4'), active_set_version_id='v1'), now=NOW)
    with pytest.raises(ValueError, match='Isolated 1x'):
        run.activate('ETHUSDT', 'perp', evaluation_id='ETHUSDT-perp-pass', now=NOW)


def test_perp_worker_job_evaluates_once_per_bar_and_never_calls_jev_for_watched_coins(tmp_path):
    from intraday.__main__ import _run_perp_setup2_cycles
    from test_multi_asset_runtime import snapshot
    large, large_rows, small_rows = bearish()
    store = IntradayStore(tmp_path/'intraday.sqlite')
    plan = active_set.equal_plan(('ETHUSDT', 'NEARUSDT', 'SOLUSDT'), anchor=FIRST)
    plan = active_set.ActiveSetPlan.model_validate({**plan.model_dump(), 'watch': {'HYPEUSDT': FIRST}})
    active_set.switch(store, plan, actor='j', reason='r', now=FIRST, execution_check=lambda: None, initial=True)
    for symbol in ('ETHUSDT', 'HYPEUSDT'):
        setup2_store.record_candles(store, symbol, 'perp', '4h', large_rows)
        setup2_store.record_candles(store, symbol, 'perp', '15m', small_rows)
    now = large[-1].available_at+timedelta(minutes=1)
    for symbol in ('ETHUSDT', 'HYPEUSDT'):
        rule = candidate_rule(store, symbol, 'perp', actor='j', reason='Setup-2 short candidate', now=now)
        store.update_scoped_rule_status(rule.rule_id, expected='queued', status='replay_passed')
        store.set_scoped_challenger(rule.rule_id, now=now)  # Soak observations follow a passed replay.

    class Refusing:
        def decide_scoped(self, *a, **k):
            raise AssertionError('no Jev call without an active full setup')

    class Market:
        def __init__(self, symbol):
            self.symbol = symbol

        def snapshot(self, symbol, *, now):
            return snapshot(symbol, DecisionScope.PERP_INTRADAY)
    markets = {s: Market(s) for s in ('ETHUSDT', 'HYPEUSDT', 'ZECUSDT')}
    fetch = lambda *a, **k: pytest.fail('stored bars are current; no fetch expected')
    import intraday.setup2_store as module
    original = module.sync
    module.sync = lambda store, symbol, market, *, anchor, now, **k: original(
        store, symbol, market, anchor=anchor, now=now, fetch_perp=fetch)
    try:
        first = _run_perp_setup2_cycles(store, Refusing(), perp_markets=markets, now=now)
        again = _run_perp_setup2_cycles(store, Refusing(), perp_markets=markets, now=now+timedelta(seconds=30))
    finally:
        module.sync = original
    assert first == {'ETHUSDT:perp_4h': 'skipped_no_setup', 'HYPEUSDT:perp_4h': 'skipped_inactive'}
    assert again == {}
