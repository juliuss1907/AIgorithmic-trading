"""Advice is route/rule/bar scoped, read-only and best effort rather than execution provenance."""
from datetime import timedelta
import json
import pytest

from intraday.contracts import DecisionScope
from intraday.execution.scoped_source import ScopedEvidenceSource
from intraday.store import IntradayStore
from test_multi_demo_runtime import NOW

ANSWERS = dict(direction={'choice':'Strong Buy','probabilities':{'Strong Buy':.49}},
    regime={'choice':'Trending Down'},toxic_flow={'noul':.32},entry_quality={'score':1},risk_level={'choice':'High'})


def add(store, *, at=NOW, symbol='ETHUSDT', market='perp', rule_id='r', label='a', answers=ANSWERS):
    return store.record_journal_signal(decision_id=label,timestamp=at,symbol=symbol,
        scope=DecisionScope.PERP_INTRADAY if market=='perp' else DecisionScope.SPOT_4H,state_snapshot='{}',
        raw_signals={"mark_price":100},jev_answers=answers,gate_passed=False,gate_reason='setup2_soak_observation_only',
        rules_version=rule_id,llm_thesis=None,market='binance_usdm_perp' if market=='perp' else 'binance_spot',
        feature_schema_version='2' if market=='perp' else '3')


def test_advice_reads_an_answer_older_than_45_seconds_without_provenance_writes(tmp_path):
    store=IntradayStore(tmp_path/'source.sqlite');add(store,at=NOW+timedelta(seconds=10))
    source=ScopedEvidenceSource(store.database,symbol='ETH',market='perp')
    # No snapshot/model-call/provider assignment is present: advice never requires them.
    before=store.counts()
    assert source.advice_for_bar(int(NOW.timestamp()*1000)-1,'r') == ANSWERS
    assert store.counts()==before
    from intraday.scoped_gate import jev_advice
    from intraday.contracts import PerpRuleParameters
    result=jev_advice(PerpRuleParameters(entry_profile='setup2_slots_v1'),ANSWERS)
    assert result['direction']=='Strong Buy' and result['reasons']==['setup2_requires_jev_sell']


@pytest.mark.parametrize('change', [dict(at=NOW-timedelta(seconds=1)),dict(at=NOW+timedelta(hours=4)),
    dict(symbol='SOLUSDT'),dict(market='spot'),dict(rule_id='other')])
def test_advice_never_uses_another_route_rule_or_h4_bar(tmp_path,change):
    store=IntradayStore(tmp_path/'source.sqlite');add(store,**change)
    source=ScopedEvidenceSource(store.database,symbol='ETH',market='perp')
    assert source.advice_for_bar(int(NOW.timestamp()*1000)-1,'r') is None


def test_bad_answers_missing_tables_and_reader_errors_are_unavailable(tmp_path,monkeypatch):
    store=IntradayStore(tmp_path/'source.sqlite');add(store,answers={'bad':True})
    source=ScopedEvidenceSource(store.database,symbol='ETH',market='perp')
    assert source.advice_for_bar(int(NOW.timestamp()*1000)-1,'r') is None
    def unavailable():raise OSError('read unavailable')
    monkeypatch.setattr(source,'connect',unavailable)
    assert source.advice_for_bar(int(NOW.timestamp()*1000)-1,'r') is None
