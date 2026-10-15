"""Offline G5/G6 rehearsal: fake account/evaluations, no credentials or network."""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from intraday import active_set
from intraday.contracts import PerpRuleParameters
from intraday.execution.allocation import DemoAllocation
from intraday.execution.journal import ExecutionJournal
from intraday.execution.multi_runtime import MultiDemoRuntime
from test_multi_demo_runtime import ACCOUNT, NOW, FakeExchange, Source
from test_slots_runtime import COINS, RULE, features
from test_setup2_parity import FIRST


@pytest.mark.parametrize('missing', ['champion', 'soak'])
def test_nine_routes_run_while_hype_spot_waits_for_its_own_evidence(tmp_path, missing):
    plan = active_set.slots_plan({s: 'both' for s in COINS}, anchors={s: FIRST for s in COINS})
    version = active_set.ActiveSetVersion(version_id='v1', seq=1, previous_version_id=None, created_at=NOW,
                                          actor='julius', reason='offline rehearsal', plan=plan)

    class WaitingSource(Source):
        def active_set(self):
            return version

        def rule(self):
            if (self.symbol, self.market) == ('HYPEUSDT', 'spot') and missing == 'champion':
                raise ValueError('no active champion for this symbol and scope')
            parameters = RULE if self.market == 'spot' else PerpRuleParameters(entry_profile='setup2_slots_v1')
            return SimpleNamespace(rule_id=self.symbol+'-'+self.market, parameters=parameters)

        def evaluation(self, evaluation_id, *, now):
            if (self.symbol, self.market) == ('HYPEUSDT', 'spot'):
                raise ValueError('Demo acceptance requires exact promoted v2 replay/soak binding; no v1 fallback')
            return super().evaluation(evaluation_id, now=now)

        def setup2_signal(self, *, now):
            return SimpleNamespace(entry=False, exit=False, size_multiplier=D('.4'), atr=D(2), features=features(30)), 1

    exchange = FakeExchange()
    exchange.balances.update({s[:-4]: D(0) for s in COINS})
    journal = ExecutionJournal(tmp_path/'execution.sqlite')
    # Fixture-only verified settings. This is never written into a VPS or a copied source/soak database.
    for s in COINS:
        journal.save_settings_request(dict(id='fixture-'+s, account=ACCOUNT.key, symbol=s, status='verified'),
                                      now=NOW, verified_leverage=1)
    exchange.leverage = 1
    run = MultiDemoRuntime(journal, ACCOUNT, exchange.venue, WaitingSource, clock=lambda: NOW)
    allocation = DemoAllocation(capital=1000, profile='setup2_slots_v1', spot_split=D('.5'), perp_split=D('.5'),
        active_set_version_id='v1', spot_emergency_stop_pct=D('.05'),
        spot_weights={s: c.spot_weight for s, c in plan.coins.items()},
        perp_weights={s: c.perp_weight for s, c in plan.coins.items()})
    configured = run.configure(allocation, now=NOW)
    assert configured['paused'] and not configured['orders_submitted']
    assert not journal.portfolio(ACCOUNT)['enabled'] and not journal.portfolio(ACCOUNT)['routes']
    assert len(allocation.routes()) == 10
    assert run._envelope(allocation) == (D(1), D('.5'), D('.5'))
    assert run._loss_limits(allocation) == (D('.97'), D('.85'))
    assert allocation.slot_cap(D(500), D(500), D('.4')) == D(500)/3*D('.4')
    for s, market in allocation.routes():
        if (s, market) == ('HYPEUSDT', 'spot'):
            continue
        assert D(run.preflight(s, market, now=NOW)['target_notional_cap']) == D(500)/3
        run.activate(s, market, evaluation_id=s+'-'+market+'-pass', now=NOW)
    before = journal.portfolio(ACCOUNT)
    assert len(before['routes']) == 9 and all(r['active'] for r in before['routes'].values())
    expected = 'no active champion' if missing == 'champion' else 'exact promoted v2'
    with pytest.raises(ValueError, match=expected):
        run.activate('HYPEUSDT', 'spot', evaluation_id='HYPEUSDT-spot-pass', now=NOW)
    assert journal.portfolio(ACCOUNT) == before
    assert run.cycle(now=NOW)['status'] == 'running'
    assert not exchange.orders
