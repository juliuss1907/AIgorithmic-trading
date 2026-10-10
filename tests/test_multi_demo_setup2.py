from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from intraday import active_set
from intraday.contracts import Regime, SpotRuleParameters
from intraday.execution.allocation import DemoAllocation
from intraday.execution.journal import ExecutionJournal
from intraday.execution.multi_runtime import MultiDemoRuntime
from test_multi_demo_runtime import ACCOUNT, NOW, FakeExchange, Source

ANCHOR = datetime(2026, 7, 2, tzinfo=timezone.utc)


def version(version_id='v1', weight=Decimal(1)):
    coin = active_set.CoinPlan(mode='spot', spot_weight=weight, perp_weight=Decimal(0), indicator_anchor=ANCHOR)
    plan = active_set.ActiveSetPlan(coins={'ETHUSDT': coin})
    return active_set.ActiveSetVersion(version_id=version_id, seq=1, previous_version_id=None, created_at=NOW,
                                       actor='julius', reason='test', plan=plan)


def setup2_source(current):
    class Setup2Source(Source):
        def active_set(self):
            return current['version']

        def spot_setup(self, *, now, rule):
            observation, bar = super().spot_setup(now=now, rule=rule)
            return SimpleNamespace(**observation.__dict__, atr=Decimal(2)), bar

        def setup2_trail(self, *, side, entry_price, signal_atr, entered_at, now):
            current['trail_calls'] = current.get('trail_calls', 0)+1
            return entry_price*current.get('trail_ratio', Decimal('.95'))

        def rule(self):
            legacy = SpotRuleParameters()
            parameters = SimpleNamespace(**legacy.model_dump(), entry_profile=current['profile'])
            parameters.allowed_regimes = (Regime.TRENDING_UP, Regime.SIDEWAYS)
            return SimpleNamespace(rule_id=self.symbol+'-'+self.market, parameters=parameters)
    return Setup2Source


def allocation(version_id='v1', weight=Decimal(1)):
    return DemoAllocation(capital=1000, spot_weights={'ETHUSDT': weight}, profile='setup2_v1',
                          spot_split=Decimal('.6'), perp_split=Decimal('.4'), active_set_version_id=version_id)


def runtime(tmp_path, current):
    exchange = FakeExchange()
    journal = ExecutionJournal(tmp_path/'execution.sqlite')
    return MultiDemoRuntime(journal, ACCOUNT, exchange.venue, setup2_source(current), clock=lambda: NOW), exchange


def test_setup2_configuration_must_equal_the_bound_active_set(tmp_path):
    current = {'version': version(), 'profile': 'setup2_v1'}
    run, _ = runtime(tmp_path, current)
    with pytest.raises(ValueError, match='current active-set version'):
        run.configure(allocation('stale'), now=NOW)
    with pytest.raises(ValueError, match='weights must equal'):
        run.configure(allocation(weight=Decimal('.5')), now=NOW)
    assert run.configure(allocation(), now=NOW)['status'] == 'configured'


def test_setup2_admits_only_setup2_champions(tmp_path):
    current = {'version': version(), 'profile': 'donchian_v1'}
    run, _ = runtime(tmp_path, current)
    run.configure(allocation(), now=NOW)
    with pytest.raises(ValueError, match='only admits Setup-2'):
        run.activate('ETHUSDT', 'spot', evaluation_id='ETHUSDT-spot-pass', now=NOW)


def test_setup2_spot_sleeve_follows_the_split_and_a_new_version_pauses_entries(tmp_path):
    current = {'version': version(), 'profile': 'setup2_v1'}
    run, exchange = runtime(tmp_path, current)
    run.configure(allocation(), now=NOW)
    run.activate('ETHUSDT', 'spot', evaluation_id='ETHUSDT-spot-pass', now=NOW)
    result = run.cycle(now=NOW)
    assert result['status'] == 'running', result
    # Legacy caps stop at 30% of capital; the 60/40 split lets one full-weight coin reach 60%.
    assert Decimal('300') < Decimal(result['spot_gross']) <= Decimal('600')
    stops = [i for i, u in exchange.orders.values() if i.symbol == 'ETHUSDT' and i.order_type == 'STOP_LOSS']
    assert stops and stops[-1].stop_price > Decimal(90)  # ATR trail (5%) instead of the 10% emergency stop.
    first = stops[-1].stop_price
    current['trail_ratio'] = Decimal('.80')  # A looser trail must never move the stop down.
    run.cycle(now=NOW)
    stops = [i for i, u in exchange.orders.values() if i.symbol == 'ETHUSDT' and i.order_type == 'STOP_LOSS']
    assert stops[-1].stop_price == first and current['trail_calls'] >= 2
    current['version'] = version('v2')
    paused = run.cycle(now=NOW)
    assert paused == {'status': 'paused', 'reason': 'active_set_changed_reconfigure'}
    assert any(i.order_type == 'STOP_LOSS' for i, u in exchange.orders.values())
