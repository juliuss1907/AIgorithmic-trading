import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from intraday import active_set
from intraday.active_set_cli import INITIAL_ANCHOR, build_plan
from intraday.contracts import DecisionScope
from intraday.execution.allocation import DemoAllocation
from intraday.portfolio_coordinator import ParentPortfolioPolicy
from intraday.portfolio_soak import run_asset_lifecycle_observation
from intraday.store import IntradayStore
from test_multi_asset_runtime import NOW, snapshot

ANCHOR = datetime(2026, 7, 2, tzinfo=timezone.utc)
COINS = ('ETHUSDT', 'NEARUSDT', 'SOLUSDT')


def plan(**kw):
    return active_set.equal_plan(COINS, anchor=ANCHOR, **kw)


def initialize(store, **kw):
    return active_set.switch(store, plan(**kw), actor='julius', reason='ADR-004', now=NOW,
                             execution_check=lambda: None, initial=True)


class Refusing:
    def decide_scoped(self, *args, **kwargs):
        raise AssertionError('an inactive coin must never reach the model')


def test_equal_plan_is_exact_and_validated():
    p = plan()
    assert sum(c.spot_weight for c in p.coins.values()) == 1 == sum(c.perp_weight for c in p.coins.values())
    assert (p.spot_split, p.perp_split, p.perp_leverage, p.perp_intraday_enabled) == (D('.60'), D('.40'), 1, False)
    assert p.scopes('ETHUSDT') == {'spot_4h', 'perp_4h'} and p.scopes('BTCUSDT') == set()
    spot_only = plan(mode='spot')
    assert spot_only.scopes('NEARUSDT') == {'spot_4h'} and spot_only.coins['NEARUSDT'].perp_weight == 0


@pytest.mark.parametrize('change,match', [
    (lambda c: {**c, 'BTCUSDT': c['ETHUSDT']}, 'one to 3 coins'),
    (lambda c: {'ETHUSDT': {**c['ETHUSDT'], 'perp_weight': '0'}}, 'positive weight exactly'),
    (lambda c: {'ETHUSDT': {**c['ETHUSDT'], 'spot_weight': '0.9'}, 'SOLUSDT': {**c['SOLUSDT'], 'spot_weight': '0.9'}},
     'spot weights must total'),
    (lambda c: {'ETHUSDT': {**c['ETHUSDT'], 'indicator_anchor': '2026-07-02T01:00:00Z'}}, 'H4 boundary'),
    (lambda c: {'ethusdt': c['ETHUSDT']}, 'canonical'),
])
def test_plan_rejects_invalid_sets(change, match):
    raw = plan().model_dump(mode='json')
    with pytest.raises(ValueError, match=match):
        active_set.ActiveSetPlan.model_validate({**raw, 'coins': change(raw['coins'])})
    with pytest.raises(ValueError, match='at most one'):
        active_set.ActiveSetPlan.model_validate({**raw, 'spot_split': '0.7'})


def test_no_version_means_legacy_and_versions_are_append_only(tmp_path):
    store = IntradayStore(tmp_path/'intraday.sqlite')
    assert active_set.current(store) is None
    assert active_set.is_active(store, 'BTCUSDT', DecisionScope.PERP_INTRADAY) is None
    assert active_set.allows(store, 'BTCUSDT', DecisionScope.PERP_INTRADAY)
    with pytest.raises(ValueError, match='initialize the active set first'):
        active_set.switch(store, plan(), actor='j', reason='r', now=NOW, execution_check=lambda: None)
    version = initialize(store)
    assert version.seq == 1 and version.previous_version_id is None and version.plan == plan()
    with pytest.raises(ValueError, match='already initialized'):
        initialize(store)
    expected = {('ETHUSDT', DecisionScope.SPOT_4H): True, ('ETHUSDT', 'perp_4h'): True,
                ('ETHUSDT', DecisionScope.PERP_INTRADAY): False, ('ETHUSDT', DecisionScope.SPOT_DAILY): False,
                ('BTCUSDT', DecisionScope.PERP_INTRADAY): False, ('HYPEUSDT', DecisionScope.SPOT_4H): False}
    assert {k: active_set.is_active(store, *k) for k in expected} == expected
    with sqlite3.connect(store.database) as c, pytest.raises(sqlite3.IntegrityError, match='append-only'):
        c.execute("UPDATE active_set_versions SET actor='x'")
    with sqlite3.connect(store.database) as c, pytest.raises(sqlite3.IntegrityError, match='append-only'):
        c.execute('DELETE FROM active_set_versions')


def test_switch_requires_paused_flat_execution_and_ready_new_coins(tmp_path):
    store = IntradayStore(tmp_path/'intraday.sqlite')

    def busy():
        raise ValueError('pause the Demo portfolio before changing the active set')
    with pytest.raises(ValueError, match='pause the Demo'):
        active_set.switch(store, plan(), actor='j', reason='r', now=NOW, execution_check=busy, initial=True)
    with pytest.raises(ValueError, match='actor and a reason'):
        active_set.switch(store, plan(), actor=' ', reason='r', now=NOW, execution_check=lambda: None, initial=True)
    first = initialize(store)
    later = NOW+timedelta(days=1)
    # Re-weighting existing coins needs no new evidence.
    reweighted = active_set.ActiveSetPlan(coins={
        'ETHUSDT': first.plan.coins['ETHUSDT'].model_copy(update={'spot_weight': D('.5'), 'perp_weight': D('.5')}),
        'NEARUSDT': first.plan.coins['NEARUSDT'].model_copy(update={'spot_weight': D('.5'), 'perp_weight': D('.5')})})
    second = active_set.switch(store, reweighted, actor='j', reason='drop SOL', now=later, execution_check=lambda: None)
    assert (second.seq, second.previous_version_id) == (2, first.version_id)
    assert active_set.is_active(store, 'SOLUSDT', DecisionScope.SPOT_4H) is False
    # A coin rotating in needs history and passing Setup-2 evidence per enabled market.
    rotated = active_set.equal_plan(('ETHUSDT', 'HYPEUSDT', 'NEARUSDT'), anchor=ANCHOR)
    with pytest.raises(ValueError, match='HYPEUSDT.*history_not_ready.*spot_setup2_gate_required'):
        active_set.switch(store, rotated, actor='j', reason='add HYPE', now=later, execution_check=lambda: None)
    assert [v.seq for v in active_set.history(store)] == [1, 2]


def test_switch_refuses_an_open_btc_parent_portfolio(tmp_path):
    from intraday.portfolio_coordinator import ParentPortfolioState
    store = IntradayStore(tmp_path/'intraday.sqlite')
    state = ParentPortfolioState(mark_price=100, perp_mark_price=100, spot_price=100, day_start_equity=10_000,
                                 high_water_mark=10_000, entries_paused=True, paper_active=True, updated_at=NOW,
                                 perp_quantity=1, perp_entry_price=100)
    store.save_parent_portfolio_state(state, event_kind='test', actor='test')
    with pytest.raises(ValueError, match='BTC parent paper'):
        initialize(store)


def test_inactive_coins_record_data_and_never_call_the_model(tmp_path):
    store = IntradayStore(tmp_path/'intraday.sqlite')
    initialize(store)
    for symbol in ('HYPEUSDT', 'ZECUSDT', 'BTCUSDT', 'ETHUSDT'):
        lifecycle = store.asset_lifecycle(symbol, DecisionScope.PERP_INTRADAY)
        if lifecycle.stage.value == 'shadow':
            store.save_asset_lifecycle(lifecycle.start_soak(), updated_at=NOW)
        status = run_asset_lifecycle_observation(store, Refusing(), snapshot(symbol, DecisionScope.PERP_INTRADAY),
                                                 scope=DecisionScope.PERP_INTRADAY, now=NOW)
        assert status == 'skipped_inactive'
    with sqlite3.connect(store.database) as c:
        assert c.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 4
        assert c.execute('SELECT COUNT(*) FROM signals').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM active_set_observations').fetchone()[0] == 4
    # A second tick in the same H4 slot adds no audit row.
    run_asset_lifecycle_observation(store, Refusing(), snapshot('ETHUSDT', DecisionScope.PERP_INTRADAY),
                                    scope=DecisionScope.PERP_INTRADAY, now=NOW+timedelta(seconds=30))
    with sqlite3.connect(store.database) as c:
        assert c.execute('SELECT COUNT(*) FROM active_set_observations').fetchone()[0] == 4


def test_split_policy_and_setup2_allocation_caps():
    legacy = ParentPortfolioPolicy()
    assert (legacy.max_gross_exposure_pct, legacy.max_isolated_margin_pct, legacy.daily_loss_limit_pct,
            legacy.max_drawdown_pct) == (0.50, 0.10, 0.015, 0.08)
    p = ParentPortfolioPolicy.from_split(D('.6'), D('.4'), 1)
    assert (p.spot_budget_pct, p.perp_budget_pct, p.spot_sleeve_target_pct, p.perp_sleeve_notional_pct) == (.6, .4, 1, 1)
    assert (p.max_gross_exposure_pct, p.max_abs_net_delta_pct, p.max_isolated_margin_pct) == (1.0, .6, .4)
    assert (p.daily_loss_limit_pct, p.max_drawdown_pct, p.leverage) == (.03, .15, 1)
    old = DemoAllocation(capital=D(900), spot_weights={'ETHUSDT': D('.5')})
    assert set(old.model_dump(mode='json')) == {'capital', 'spot_emergency_stop_pct', 'spot_weights', 'perp_weights'}
    assert old.target_cap('ETHUSDT', 'spot') == D(900)*D('.30')*D('.5')
    third = D(1)/3
    new = DemoAllocation(capital=D(900), spot_weights={'ETHUSDT': third}, perp_weights={'ETHUSDT': third},
                         profile='setup2_v1', spot_split=D('.6'), perp_split=D('.4'), active_set_version_id='v')
    assert new.target_cap('ETHUSDT', 'spot') == D(900)*D('.6')*third
    assert DemoAllocation.model_validate_json(new.model_dump_json()) == new
    with pytest.raises(ValueError, match='setup2 profile'):
        DemoAllocation(capital=D(900), spot_weights={'ETHUSDT': D('.5')}, spot_split=D('.6'))


def test_execution_check_reads_the_journal_without_writing(tmp_path):
    from intraday.execution.journal import ExecutionJournal
    from intraday.execution.multi_runtime import require_paused_and_flat
    path = tmp_path/'binance-demo.sqlite3'
    ExecutionJournal(path)
    require_paused_and_flat(ExecutionJournal(path, read_only=True))


def args(**kw):
    base = dict(coins=None, coin=[], mode='both', split='60/40', watch=[])
    return argparse.Namespace(**{**base, **kw})


def test_cli_plans_equal_or_explicit_weights_with_anchors():
    initial = build_plan(args(coins='ETH,NEAR,SOL'), None, NOW)
    assert initial == plan() and INITIAL_ANCHOR == ANCHOR
    version = active_set.ActiveSetVersion(version_id='v', seq=1, previous_version_id=None, created_at=NOW,
                                          actor='j', reason='r', plan=initial)
    custom = build_plan(args(coin=['ETH:both:0.5:0.5', 'HYPE:perp:0:0.5'], split='50/50'), version, NOW)
    assert custom.coins['ETHUSDT'].indicator_anchor == ANCHOR
    assert custom.coins['HYPEUSDT'].indicator_anchor == NOW-600*active_set.H4
    assert (custom.spot_split, custom.perp_split) == (D('.5'), D('.5'))
    with pytest.raises(ValueError, match='either --coins'):
        build_plan(args(coins='ETH', coin=['ETH:both:1:1']), None, NOW)


def test_watched_coins_soak_rule_only_and_never_overlap_active_coins(tmp_path):
    raw = plan().model_dump(mode='json')
    with pytest.raises(ValueError, match='either active or watched'):
        active_set.ActiveSetPlan.model_validate({**raw, 'watch': {'ETHUSDT': ANCHOR}})
    version = active_set.ActiveSetVersion(version_id='v', seq=1, previous_version_id=None, created_at=NOW,
                                          actor='j', reason='r', plan=plan())
    watched = build_plan(args(coins='ETH,NEAR,SOL', watch=['HYPE']), version, NOW)
    assert watched.watch == {'HYPEUSDT': NOW-600*active_set.H4} and watched.anchor('HYPEUSDT') == NOW-600*active_set.H4
    store = IntradayStore(tmp_path/'intraday.sqlite')
    active_set.switch(store, watched, actor='j', reason='watch HYPE', now=NOW, execution_check=lambda: None, initial=True)
    assert active_set.watched(store, 'HYPEUSDT') and not active_set.allows(store, 'HYPEUSDT', DecisionScope.SPOT_4H)
    blockers = active_set.rotation_blockers(store, 'HYPEUSDT', watched.coins['ETHUSDT'].model_copy(
        update={'indicator_anchor': watched.watch['HYPEUSDT']}), NOW)
    assert blockers == ['history_not_ready', 'perp_setup2_gate_required', 'spot_setup2_gate_required']
