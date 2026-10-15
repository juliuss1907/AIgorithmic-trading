"""Slot design active set and allocation (ADR-007): five coins, 50/50 sleeves, per-coin modes, slot sizing."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from intraday import active_set, setup2
from intraday.active_set_cli import build_plan
from intraday.execution.allocation import DemoAllocation
from intraday.store import IntradayStore
from test_multi_asset_runtime import NOW

ANCHOR = datetime(2025, 6, 1, tzinfo=timezone.utc)
FIVE = ('ETHUSDT', 'HYPEUSDT', 'NEARUSDT', 'SOLUSDT', 'ZECUSDT')


def five(**modes):
    chosen = {s: 'both' for s in FIVE} | {'HYPEUSDT': 'perp'} | modes
    return active_set.slots_plan(chosen, anchors={s: ANCHOR for s in chosen})


def test_slot_plan_holds_five_coins_with_equal_exact_weights_per_enabled_market():
    plan = five()
    assert (plan.rule_profile, plan.spot_split, plan.perp_split) == ('setup2_slots_v1', D('.5'), D('.5'))
    assert sum(c.spot_weight for c in plan.coins.values()) == 1 == sum(c.perp_weight for c in plan.coins.values())
    assert plan.coins['HYPEUSDT'].mode == 'perp' and plan.coins['HYPEUSDT'].spot_weight == 0
    assert plan.scopes('HYPEUSDT') == {'perp_4h'} and plan.scopes('ZECUSDT') == {'spot_4h', 'perp_4h'}
    assert {c.spot_weight for s, c in plan.coins.items() if s != 'HYPEUSDT'} == {D('.25')}


def test_limits_follow_the_profile():
    raw = five().model_dump(mode='json')
    with pytest.raises(ValueError, match='one to 3 coins'):
        active_set.ActiveSetPlan.model_validate({**raw, 'rule_profile': 'setup2_v1', 'spot_split': '0.6',
                                                 'perp_split': '0.4'})
    with pytest.raises(ValueError, match='50/50'):
        active_set.ActiveSetPlan.model_validate({**raw, 'spot_split': '0.6', 'perp_split': '0.4'})
    extra = {**raw['coins'], 'BTCUSDT': raw['coins']['ETHUSDT']}
    with pytest.raises(ValueError, match='one to 5 coins'):
        active_set.ActiveSetPlan.model_validate({**raw, 'coins': extra})


def arguments(**kw):
    base = dict(coins=None, mode='both', coin=[], split=None, profile='setup2_slots_v1', watch=[], anchor=None)
    return argparse.Namespace(**(base | kw))


def test_cli_builds_the_slot_plan_with_per_coin_modes_and_an_early_anchor():
    plan = build_plan(arguments(coin=['ETH:both', 'NEAR:both', 'SOL:both', 'ZEC:both', 'HYPE:perp'],
                                anchor='2025-06-01'), None, NOW)
    assert plan.rule_profile == 'setup2_slots_v1' and plan.anchor('HYPEUSDT') == ANCHOR
    assert plan.coins['HYPEUSDT'].markets == {'perp'}
    equal = build_plan(arguments(coins='ETH,NEAR,SOL', anchor='2025-06-01'), None, NOW)
    assert (equal.spot_split, equal.perp_split) == (D('.5'), D('.5')) and len(equal.coins) == 3
    with pytest.raises(ValueError, match='H4 boundary'):
        build_plan(arguments(coins='ETH', anchor='2025-06-01T01:00:00'), None, NOW)
    with pytest.raises(ValueError, match='COIN:MODE'):
        build_plan(arguments(coin=['ETH:both:0.5:0.5']), None, NOW)
    legacy = build_plan(arguments(coins='ETH,NEAR,SOL', profile='setup2_v1'), None, NOW)
    assert legacy.rule_profile == 'setup2_v1' and (legacy.spot_split, legacy.perp_split) == (D('.6'), D('.4'))


def test_an_audited_switch_records_the_slot_plan(tmp_path):
    store = IntradayStore(tmp_path/'intraday.sqlite')
    version = active_set.switch(store, five(), actor='julius', reason='ADR-007', now=NOW,
                                execution_check=lambda: None, initial=True)
    assert version.plan.rule_profile == 'setup2_slots_v1' and len(version.plan.coins) == 5
    assert active_set.is_active(store, 'HYPEUSDT', 'perp_4h') is True
    assert active_set.is_active(store, 'HYPEUSDT', 'spot_4h') is False


def allocation(**kw):
    plan = five()
    fields = dict(capital=1000, profile='setup2_slots_v1', spot_split=D('.5'), perp_split=D('.5'),
                  active_set_version_id='v1', spot_weights={s: c.spot_weight for s, c in plan.coins.items()
                                                            if c.spot_weight > 0},
                  perp_weights={s: c.perp_weight for s, c in plan.coins.items() if c.perp_weight > 0})
    return DemoAllocation(**(fields | kw))


def test_allocation_requires_equal_sleeves_and_sizes_a_third_of_the_realized_sleeve():
    with pytest.raises(ValueError, match='50/50'):
        allocation(spot_split=D('.6'), perp_split=D('.4'))
    a = allocation()
    assert a.is_slots and not allocation(profile='setup2_v1', spot_split=D('.6'), perp_split=D('.4')).is_slots
    # 1/3 of a 600 sleeve, scaled by the ATR multiplier, bounded by the free principal.
    assert a.slot_cap(D(600), D(600), D('.8')) == D(600)/3*D('.8')
    assert a.slot_cap(D(600), D(100), D(1)) == D(100)
    assert a.slot_cap(D(600), D(-5), D(1)) == 0
    assert setup2.SLOT_DIVISOR == 3 and setup2.SLOT_MAX_POSITIONS == 3 and setup2.ROTATION_MARGIN == 5
    with pytest.raises(ValueError, match='slot profile'):
        allocation(profile='setup2_v1', spot_split=D('.6'), perp_split=D('.4')).slot_cap(D(1), D(1), D(1))


@pytest.mark.parametrize("action", ["init", "switch"])
def test_active_set_help_explains_the_net_return_waiver(action, capsys):
    from intraday.__main__ import _parser

    with pytest.raises(SystemExit) as stopped:
        _parser().parse_args(["active-set", action, "--help"])
    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "--waive-net-return" in output and "-5% floor" in output
