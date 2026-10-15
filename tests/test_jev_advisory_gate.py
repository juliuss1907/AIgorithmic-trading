"""Explicit Demo-only advice mode; old payloads and deterministic risk stay intact."""
import json
from types import SimpleNamespace

import pytest

from intraday import active_set
from intraday.__main__ import _parser
from intraday.contracts import DecisionScope, Direction, PerpRuleParameters, Regime, RiskLevel, SpotRuleParameters
from intraday.execution.allocation import DemoAllocation
from intraday.execution.cli import _allocation
from intraday.portfolio_coordinator import ParentPortfolioCoordinator, ParentPortfolioPolicy
from intraday.scoped_gate import ScopedEntryGate
from intraday.store import IntradayStore
from test_scoped_entry_gate import NOW, decision, state


def allocation(profile='setup2_slots_v1', **extra):
    fields = {} if profile == 'legacy' else dict(profile=profile, spot_split='.5' if profile.endswith('slots_v1') else '.6',
        perp_split='.5' if profile.endswith('slots_v1') else '.4', active_set_version_id='v1')
    return DemoAllocation(capital=1000, spot_weights={'ETHUSDT': 1}, **fields, **extra)


BASE_PAYLOADS = {'legacy': '{"capital":"1000","spot_emergency_stop_pct":"0.10","spot_weights":{"ETHUSDT":"1"},"perp_weights":{}}', 'setup2_v1': '{"capital":"1000","spot_emergency_stop_pct":"0.10","spot_weights":{"ETHUSDT":"1"},"perp_weights":{},"profile":"setup2_v1","spot_split":"0.6","perp_split":"0.4","active_set_version_id":"v1"}', 'setup2_slots_v1': '{"capital":"1000","spot_emergency_stop_pct":"0.10","spot_weights":{"ETHUSDT":"1"},"perp_weights":{},"profile":"setup2_slots_v1","spot_split":"0.5","perp_split":"0.5","active_set_version_id":"v1"}'}


@pytest.mark.parametrize('profile', list(BASE_PAYLOADS))
def test_default_gate_allocation_keeps_previous_payload_bytes(profile):
    plan = allocation(profile)
    assert plan.jev_mode == 'gate'
    assert plan.model_dump_json() == BASE_PAYLOADS[profile]
    assert allocation(profile, jev_mode='gate').model_dump_json() == BASE_PAYLOADS[profile]
    assert DemoAllocation.model_validate_json(plan.model_dump_json()).jev_mode == 'gate'


def test_advisory_allocation_only_admits_slots_and_survives_serialization():
    plan = allocation(jev_mode='advisory')
    assert json.loads(plan.model_dump_json())['jev_mode'] == 'advisory'
    assert DemoAllocation.model_validate_json(plan.model_dump_json()) == plan
    for profile in ('legacy', 'setup2_v1'):
        with pytest.raises(ValueError, match='advisory.*setup2_slots_v1'):
            allocation(profile, jev_mode='advisory')


def test_cli_uses_the_explicit_jev_mode_in_both_allocation_paths(tmp_path):
    command = ['execution', 'demo', 'configure', '--source-database', str(tmp_path/'source.sqlite'), '--capital', '1000']
    parser = _parser()
    assert parser.parse_args(command).jev_mode == 'gate'
    manual = parser.parse_args(command+['--spot-weight', 'ETH=1', '--jev-mode', 'advisory'])
    with pytest.raises(ValueError, match='advisory.*setup2_slots_v1'):
        _allocation(manual, None)
    store = IntradayStore(tmp_path/'source.sqlite')
    plan = active_set.slots_plan({'ETHUSDT': 'both'}, anchors={'ETHUSDT': NOW.replace(hour=0)})
    active_set.switch(store, plan, actor='julius', reason='fixture', now=NOW, execution_check=lambda: None, initial=True)
    args = parser.parse_args(command+['--from-active-set', '--jev-mode', 'advisory'])
    result = _allocation(args, SimpleNamespace(path=store.database))
    assert result.jev_mode == 'advisory' and result.profile == 'setup2_slots_v1'
    assert str(result.spot_emergency_stop_pct) == '0.05'


def gate():
    return ScopedEntryGate(ParentPortfolioCoordinator(ParentPortfolioPolicy.from_split('.5', '.5', 1)))


def rule(market, profile='setup2_slots_v1'):
    return (SpotRuleParameters(entry_profile=profile, entry_window=25 if profile.endswith('slots_v1') else 30,
                               exit_window=10) if market == 'spot' else PerpRuleParameters(entry_profile=profile))


def entry(market, made, *, triggered=True, advisory=True, portfolio=None, **extra):
    g, p = gate(), portfolio or state()
    return (g.spot_entry(p, made, rule(market), donchian_entry=triggered, size_multiplier=.4,
                        scope=DecisionScope.SPOT_4H, advisory=advisory, **extra) if market == 'spot' else
            g.perp_setup2_entry(p, made, rule(market), setup_entry=triggered, size_multiplier=.4,
                               advisory=advisory, **extra))


@pytest.mark.parametrize('market', ['spot', 'perp'])
@pytest.mark.parametrize('missing', [False, True])
def test_advice_cannot_veto_a_triggered_rule_or_pick_its_direction(market, missing):
    made = None if missing else decision(Direction.STRONG_BUY, .49).model_copy(
        update=dict(risk_level=RiskLevel.HIGH, toxic_flow=.32, entry_quality=1, regime=Regime.TRENDING_DOWN))
    result = entry(market, made)
    assert result.allowed and result.target_notional == (2000 if market == 'spot' else -2000)
    if made is not None:
        assert not entry(market, made, advisory=False).allowed


@pytest.mark.parametrize('market,reason', [('spot', 'donchian_entry_not_triggered'), ('perp', 'no_setup2_short_setup')])
def test_advisory_still_requires_the_rule_trigger(market, reason):
    result = entry(market, None, triggered=False)
    assert not result.allowed and result.reason_codes == (reason,)


@pytest.mark.parametrize('market', ['spot', 'perp'])
@pytest.mark.parametrize('portfolio,reason', [(state(realized_pnl=-400), 'daily_loss_limit'),
    (state(high_water_mark=12000), 'parent_drawdown_limit'), (state(entries_paused=True), 'entries_paused')])
def test_advisory_keeps_daily_loss_drawdown_and_pause(market, portfolio, reason):
    result = entry(market, None, portfolio=portfolio)
    assert not result.allowed and reason in result.reason_codes


def test_advisory_keeps_margin_and_other_profiles_cannot_opt_in_at_the_gate():
    assert 'isolated_margin_limit' in entry('perp', None, projected_isolated_margin_pct=.51).reason_codes
    for profile in ('donchian_v1', 'setup2_v1'):
        with pytest.raises(ValueError, match='advisory'):
            gate().spot_entry(state(), None, rule('spot', profile), donchian_entry=True,
                              scope=DecisionScope.SPOT_4H, advisory=True)
        with pytest.raises(ValueError, match='advisory'):
            gate().perp_setup2_entry(state(), None, rule('perp', 'jev_intraday_v1' if profile == 'donchian_v1' else profile), setup_entry=True,
                                     size_multiplier=1, advisory=True)


@pytest.mark.parametrize('market', ['spot', 'perp'])
@pytest.mark.parametrize('made', [decision(Direction.STRONG_BUY, .49).model_copy(update=dict(risk_level=RiskLevel.HIGH, toxic_flow=.32)),
    decision(Direction.SELL, .95, Regime.TRENDING_DOWN), decision(Direction.BUY, .95)])
def test_advice_reports_exactly_the_old_gate_reasons(market, made):
    from intraday.scoped_gate import jev_advice
    expected = entry(market, made, advisory=False)
    advice = jev_advice(rule(market), made)
    assert advice == dict(direction=made.direction.value, confidence=made.direction_confidence,
                          would_allow=expected.allowed, reasons=list(expected.reason_codes))
