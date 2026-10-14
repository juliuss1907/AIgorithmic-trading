"""Retiring a legacy challenger closes its v2 gate campaign; an orphaned campaign can be closed explicitly."""
import json
from datetime import timedelta

import pytest

from intraday import active_set
from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
from intraday.replay_v2.gate_repository import GateRepository
from intraday.setup2_cli import candidate_rule, retire_campaign
from intraday.store import IntradayStore
from test_setup2_parity import FIRST

NOW = FIRST+timedelta(days=200)


def challenger_with_campaign(tmp_path):
    store = IntradayStore(tmp_path/'intraday.sqlite')
    active_set.switch(store, active_set.equal_plan(('ETHUSDT', 'NEARUSDT', 'SOLUSDT'), anchor=FIRST), actor='j',
                      reason='retire test', now=FIRST, execution_check=lambda: None, initial=True)
    legacy = ScopedRuleCandidate.create(rule_id='ethusdt-spot_4h-candidate-legacy', parent_rule_id='bootstrap',
        thesis_id='legacy', symbol='ETHUSDT', scope=DecisionScope.SPOT_4H, parameters=SpotRuleParameters(),
        created_at=NOW-timedelta(days=10), model_ref='operator/test', prompt_version='legacy-v1')
    store.register_scoped_rule(legacy)
    store.update_scoped_rule_status(legacy.rule_id, expected='queued', status='replay_passed')
    store.set_scoped_challenger(legacy.rule_id, now=NOW-timedelta(days=9))
    repository = GateRepository(store)
    repository.install()
    with store._connect() as c:
        c.execute("INSERT INTO replay_gate_campaigns VALUES (?,?,?,?,?,'active',?)",
                  ('campaign-1', 'ETHUSDT', 'spot_4h', legacy.rule_id, (NOW-timedelta(days=9)).isoformat(),
                   json.dumps({'campaign_id': 'campaign-1', 'candidate_id': legacy.rule_id})))
    return store, repository, legacy


def test_retire_open_closes_the_rejected_challengers_v2_campaign(tmp_path):
    store, repository, legacy = challenger_with_campaign(tmp_path)
    assert repository.current('ETHUSDT', DecisionScope.SPOT_4H)['status'] == 'active'
    new = candidate_rule(store, 'ETHUSDT', 'spot', actor='julius', reason='ADR-007 candidate', now=NOW,
                         retire_open=True, profile='setup2_slots_v1')
    assert repository.current('ETHUSDT', DecisionScope.SPOT_4H)['status'] == 'rejected'
    assert new.parameters.entry_profile == 'setup2_slots_v1'


def test_an_orphaned_campaign_is_closed_only_when_its_candidate_is_already_rejected(tmp_path):
    store, repository, legacy = challenger_with_campaign(tmp_path)
    with pytest.raises(ValueError, match='rejected candidate'):
        retire_campaign(store, 'ETHUSDT', 'spot', actor='julius', reason='cleanup', now=NOW)
    store.reject_scoped_challenger(legacy.rule_id, now=NOW)           # the old path: rule rejected, campaign left active
    assert repository.current('ETHUSDT', DecisionScope.SPOT_4H)['status'] == 'active'
    result = retire_campaign(store, 'ETHUSDT', 'spot', actor='julius', reason='orphan after retire-open', now=NOW)
    assert result['closed'] == 1 and result['candidate_id'] == legacy.rule_id
    assert repository.current('ETHUSDT', DecisionScope.SPOT_4H)['status'] == 'rejected'
    with pytest.raises(ValueError, match='no active v2 campaign'):
        retire_campaign(store, 'ETHUSDT', 'spot', actor='julius', reason='again', now=NOW)
    with pytest.raises(ValueError, match='actor and a reason'):
        retire_campaign(store, 'ETHUSDT', 'spot', actor=' ', reason='x', now=NOW)
