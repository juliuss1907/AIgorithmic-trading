"""Rule/bar identity, bounded advice wait and unchanged slot/risk mechanics; offline exchanges only."""
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from intraday import active_set
from intraday.contracts import Direction, PerpRuleParameters, Regime, RiskLevel
from intraday.execution.allocation import DemoAllocation
from intraday.execution.journal import ExecutionJournal
from intraday.execution.multi_runtime import MultiDemoRuntime, identity
from test_multi_demo_runtime import ACCOUNT, NOW, FakeExchange, Source
from test_slots_runtime import PROFILE, RULE, features
from test_setup2_parity import FIRST
from test_scoped_entry_gate import decision

COINS = ('ETHUSDT', 'NEARUSDT', 'SOLUSDT', 'ZECUSDT')


def opposition():
    return decision(Direction.STRONG_BUY, .49).model_copy(update=dict(risk_level=RiskLevel.HIGH, toxic_flow=.32))


def build(tmp_path, market='spot', mode='advisory', *, rule_id=None):
    plan = active_set.slots_plan({s: market for s in COINS}, anchors={s: FIRST for s in COINS})
    version = active_set.ActiveSetVersion(version_id='v1', seq=1, previous_version_id=None, created_at=NOW,
                                          actor='j', reason='fixture', plan=plan)
    exchange = FakeExchange(); exchange.leverage = 1
    exchange.balances.update({s[:-4]: D(0) for s in COINS})
    world = dict(now=NOW, bar=int(NOW.timestamp()*1000)-1, signals={}, answers={}, default_advice=None,
                 close=D(100), advice_error=None, gate_error=None, gate_direction=None)

    class SlotSource(Source):
        def active_set(self): return version

        def rule(self):
            params = RULE if self.market == 'spot' else PerpRuleParameters(entry_profile=PROFILE,
                allowed_regimes=(Regime.TRENDING_DOWN, Regime.SIDEWAYS))
            return SimpleNamespace(rule_id=rule_id or self.symbol+'-'+self.market, parameters=params)

        def setup2_signal(self, *, now):
            data = world['signals'].get(self.symbol, {})
            return SimpleNamespace(entry=data.get('entry', False), exit=data.get('exit', False),
                size_multiplier=D('.4'), atr=D(2), close=world['close'], features=features(data.get('adx', 0),
                plus=30 if market == 'spot' else 10, minus=10 if market == 'spot' else 30)), world['bar']

        def setup2_trail(self, *, side, entry_price, signal_atr, entered_at, now):
            return entry_price*(D('.95') if side == 1 else D('1.05'))

        def advice_for_bar(self, bar, requested_rule):
            assert bar == world['bar'] and requested_rule == self.rule().rule_id
            if world['advice_error']: raise world['advice_error']
            return world['answers'].get(self.symbol, world['default_advice'])

        def latest_decision(self, *, now):
            if world['gate_error']: raise world['gate_error']
            made = decision(world['gate_direction'] or (Direction.BUY if market == 'spot' else Direction.SELL),
                .95, Regime.TRENDING_UP if market == 'spot' else Regime.TRENDING_DOWN).model_copy(update={'created_at': now})
            return SimpleNamespace(features={'mark_price': float(exchange.mark), 'reference_price': float(exchange.mark)}), made, self.rule().rule_id

    journal = ExecutionJournal(tmp_path/'execution.sqlite')
    for s in COINS:
        journal.save_settings_request(dict(id='fixture-'+s, account=ACCOUNT.key, symbol=s, status='verified'),
                                      now=NOW, verified_leverage=1)
    run = MultiDemoRuntime(journal, ACCOUNT, exchange.venue, SlotSource, clock=lambda: world['now'])
    allocation = DemoAllocation(capital=1000, profile=PROFILE, spot_split='.5', perp_split='.5',
        active_set_version_id='v1', spot_emergency_stop_pct='.05', jev_mode=mode,
        **{market+'_weights': {s: getattr(c, market+'_weight') for s,c in plan.coins.items()}})
    run.configure(allocation, now=NOW)
    for s in COINS:run.activate(s, market, evaluation_id=s+'-'+market+'-pass', now=NOW)
    return run, exchange, world, allocation


def entries(exchange):
    return [i for i,u in exchange.orders.values() if i.order_type == 'MARKET' and not (i.source_id or '').startswith('close:')]


def next_bar(run, world):
    world['now'] += timedelta(hours=4); world['bar'] = int(world['now'].timestamp()*1000)-1
    return run.cycle(now=world['now'])


@pytest.mark.parametrize('market', ['spot', 'perp'])
@pytest.mark.parametrize('error', [None, RuntimeError('circuit_open'), ValueError('insufficient_credits')])
def test_missing_or_failed_jev_waits_at_most_120_seconds_then_enters(market, error, tmp_path):
    run, exchange, world, _ = build(tmp_path, market)
    world.update(signals={'ETHUSDT': dict(entry=True, adx=40)}, advice_error=error)
    for seconds in (0, 119):
        world['now'] = NOW+timedelta(seconds=seconds)
        report = run.cycle(now=world['now'])
        assert {'status': 'waiting_for_jev_answers'} in report['routes'] and not entries(exchange)
    world['now'] = NOW+timedelta(seconds=120)
    report = run.cycle(now=world['now'])
    assert report['status'] == 'running' and len(entries(exchange)) == 1, report
    intent = entries(exchange)[0]
    assert intent.side == ('BUY' if market == 'spot' else 'SELL')
    assert run._portfolio()['routes'][market+':ETHUSDT']['entry_jev'] == 'unavailable'
    assert run._portfolio()['slot_selection']['steps'][0]['jev'] == 'unavailable'
    assert any(r.get('jev') == 'unavailable' and r['status'] == 'FILLED' for r in report['routes'])


@pytest.mark.parametrize('market', ['spot', 'perp'])
def test_jev_opposition_is_audited_but_never_blocks_the_rule(market, tmp_path):
    run, exchange, world, _ = build(tmp_path, market)
    world.update(signals={'ETHUSDT': dict(entry=True, adx=40)}, default_advice=opposition())
    report = run.cycle(now=world['now'])
    assert len(entries(exchange)) == 1, report
    advice = run._portfolio()['routes'][market+':ETHUSDT']['entry_jev']
    assert advice['direction'] == 'Strong Buy' and advice['confidence'] == .49 and not advice['would_allow']
    assert advice['reasons']
    assert run._portfolio()['slot_selection']['steps'][0]['jev'] == advice
    assert next(r['jev'] for r in report['routes'] if r['status'] == 'FILLED') == advice
    stops = [i for i,u in exchange.orders.values() if i.order_type != 'MARKET']
    assert len(stops) == 1 and stops[0].side == ('SELL' if market == 'spot' else 'BUY')


@pytest.mark.parametrize('market', ['spot', 'perp'])
def test_a_late_answer_restart_and_reactivation_cannot_reenter_the_same_bar(market, tmp_path):
    run, exchange, world, _ = build(tmp_path, market)
    world.update(signals={'ETHUSDT': dict(entry=True, adx=40)}, now=NOW+timedelta(seconds=121))
    run.cycle(now=world['now']);intent = entries(exchange)[0];p = run._portfolio()
    key = f'setup2:ETHUSDT-{market}:{world["bar"]}'
    assert intent.source_id == key and not key.startswith('close:')
    assert intent.intent_id == identity(p['campaign_id'], 'ETHUSDT', market, key, 'entry')
    assert run.journal.order(intent.account, intent.intent_id)[0].source_id == key
    world['answers']['ETHUSDT'] = opposition()
    restarted = MultiDemoRuntime(run.journal, ACCOUNT, exchange.venue, run.source_factory, clock=lambda: world['now'])
    restarted.cycle(now=world['now'])
    restarted.flatten(now=world['now'])
    restarted.activate('ETHUSDT', market, evaluation_id='ETHUSDT-'+market+'-pass', now=world['now'])
    restarted.cycle(now=world['now'])
    assert len(entries(exchange)) == 1 and entries(exchange)[0].intent_id == intent.intent_id


def test_full_length_rule_identity_fits_the_execution_journal(tmp_path):
    run, exchange, world, _ = build(tmp_path, rule_id='r'*128)
    world.update(signals={'ETHUSDT': dict(entry=True, adx=40)}, now=NOW+timedelta(seconds=121))
    report = run.cycle(now=world['now'])
    assert report['status'] == 'running' and len(entries(exchange)) == 1, report
    assert entries(exchange)[0].source_id == f'setup2:{"r"*128}:{world["bar"]}'


@pytest.mark.parametrize('market', ['spot', 'perp'])
def test_advisory_price_guard_uses_the_h4_close_even_without_a_jev_snapshot(market, tmp_path):
    run, exchange, world, _ = build(tmp_path, market)
    world.update(signals={'ETHUSDT': dict(entry=True, adx=40)}, default_advice=opposition())
    exchange.mark = D('102')
    report = run.cycle(now=world['now'])
    assert not entries(exchange)
    assert any(r.get('reason') == 'price_gap' or r.get('status') == 'price_gap' for r in report['routes'])
    assert run._portfolio()['slot_selection']['steps'][0]['jev']['would_allow'] is False


@pytest.mark.parametrize('market', ['spot', 'perp'])
def test_advisory_rotation_still_requires_more_than_five_adx_points(market, tmp_path):
    run, exchange, world, _ = build(tmp_path, market)
    world['default_advice'] = opposition()
    world['signals'] = {s: dict(entry=True, adx=a) for s,a in [('ETHUSDT',20),('NEARUSDT',30),('SOLUSDT',40)]}
    run.cycle(now=world['now']);assert len(entries(exchange)) == 3
    exchange.mark = D('100.5') if market == 'spot' else D('99.5')
    world['signals'] = {s:dict(adx=a) for s,a in [('ETHUSDT',20),('NEARUSDT',30),('SOLUSDT',40)]}
    world['signals']['ZECUSDT'] = dict(entry=True,adx=25)
    report = next_bar(run,world)
    assert any(r.get('reason') == 'rotation_margin' for r in report['routes']) and len(entries(exchange)) == 3
    world['signals']['ZECUSDT']['adx'] = 26
    report = next_bar(run,world)
    assert len(entries(exchange)) == 4 and any(r['status'] == 'rotation_exit' for r in report['routes'])
    assert any(s['kind'] == 'rotate' and not s['jev']['would_allow'] for s in run._portfolio()['slot_selection']['steps'])


@pytest.mark.parametrize('market', ['spot', 'perp'])
def test_gate_mode_keeps_jev_veto_and_the_existing_600_second_grace(market, tmp_path):
    run, exchange, world, _ = build(tmp_path, market, mode='gate')
    world['signals'] = {'ETHUSDT': dict(entry=True, adx=40)}
    world['gate_error'] = ValueError('no primary answer')
    for seconds in (120,599):
        world['now'] = NOW+timedelta(seconds=seconds)
        assert {'status':'waiting_for_jev_answers'} in run.cycle(now=world['now'])['routes']
        assert not entries(exchange)
    world['gate_error'] = None
    world['gate_direction'] = Direction.SELL if market == 'spot' else Direction.BUY
    report = next_bar(run,world)
    assert any(r['status'] == 'not_authorized' for r in report['routes']) and not entries(exchange)


def test_changing_jev_mode_still_requires_paused_and_flat(tmp_path):
    run, exchange, world, allocation = build(tmp_path)
    gated = allocation.model_copy(update={'jev_mode':'gate'})
    with pytest.raises(ValueError,match='paused flat'):
        run.configure(gated,now=world['now'])
    world.update(signals={'ETHUSDT':dict(entry=True,adx=40)},now=NOW+timedelta(seconds=121))
    run.cycle(now=world['now']);run.pause(now=world['now'])
    with pytest.raises(ValueError,match='flatten'):
        run.configure(gated,now=world['now'])
    run.flatten(now=world['now'])
    assert run.configure(gated,now=world['now'])['allocation'].get('jev_mode','gate') == 'gate'
