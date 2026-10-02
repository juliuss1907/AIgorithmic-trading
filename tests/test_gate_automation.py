from datetime import timedelta

import pytest

from intraday.contracts import DecisionScope
from intraday.replay_v2.automation import set_policy, projection, run_tick
from intraday.replay_v2.gate_repository import GateRepository
from intraday.replay_v2.lifecycle import replay_gate, start_gate_soak, evaluate_gate_soak
from test_replay_gate_lifecycle import setup
from test_replay_v2_data import dump
from test_spot_4h_lifecycle import NOW


def test_optional_policy_reads_never_write_and_enabling_preserves_campaign(tmp_path):
    store, rule, root = setup(tmp_path)
    before = dump(store.database)
    assert projection(store, rule.symbol, rule.scope, now=NOW)['status'] == 'disabled'
    assert dump(store.database) == before
    evaluation = replay_gate(store, rule.rule_id, now=NOW, report_dir=root)
    campaign = start_gate_soak(store, rule.rule_id, evaluation_id=evaluation.evaluation_id, now=NOW, report_dir=root)
    set_policy(store, 'all', enabled=True, now=NOW)
    state = projection(store, rule.symbol, rule.scope, now=NOW)
    assert state['phase'] == 'soak'
    assert state['next_run_at'] == (NOW+timedelta(days=14)).isoformat()
    assert GateRepository(store).current(rule.symbol,rule.scope) == campaign
    set_policy(store, 'all', enabled=False, now=NOW)
    assert projection(store, rule.symbol, rule.scope, now=NOW)['status'] == 'paused'
    assert GateRepository(store).current(rule.symbol,rule.scope) == campaign


def test_pass_stops_and_alerts_once_without_starting_validation(tmp_path):
    store, rule, root = setup(tmp_path)
    set_policy(store, 'spot', enabled=True, now=NOW)
    result = run_tick(store, now=NOW, report_dir=root)
    assert result[f'{rule.symbol}:spot']['status'] == 'pass'
    assert GateRepository(store).current(rule.symbol,rule.scope) is None
    assert store.scoped_rule_status(rule.rule_id) == 'queued'
    assert projection(store,rule.symbol,rule.scope,now=NOW)['status'] == 'passed_waiting_operator'
    before = dump(store.database)
    run_tick(store,now=NOW+timedelta(days=30),report_dir=root)
    assert dump(store.database) == before
    assert len([a for a in store.list_operator_alerts() if a['kind']=='gate_v2_pass']) == 1


def test_manual_evaluation_counts_and_window_is_cumulative(tmp_path):
    store, rule, root = setup(tmp_path)
    replay = replay_gate(store,rule.rule_id,now=NOW,report_dir=root)
    start_gate_soak(store,rule.rule_id,evaluation_id=replay.evaluation_id,now=NOW,report_dir=root)
    set_policy(store,'spot',enabled=True,now=NOW)
    early = evaluate_gate_soak(store,rule.rule_id,now=NOW+timedelta(days=13),report_dir=root)
    assert early.status == 'deferred'
    state = projection(store,rule.symbol,rule.scope,now=NOW+timedelta(days=14))
    assert state['next_run_at'] == (NOW+timedelta(days=20)).isoformat()
    assert state['window_start'] == NOW.isoformat()
    assert state['status'] == 'waiting'


def test_interval_is_fixed_and_policy_survives_restart(tmp_path):
    store, rule, root = setup(tmp_path)
    with pytest.raises(ValueError,match='7'):
        set_policy(store,'all',enabled=True,now=NOW,interval_days=1)
    set_policy(store,'all',enabled=True,now=NOW)
    from intraday.store import IntradayStore
    reader = IntradayStore(store.database,read_only=True)
    before = dump(store.database)
    assert projection(reader,rule.symbol,rule.scope,now=NOW)['status'] == 'due'
    assert dump(store.database) == before


def perp_setup(tmp_path):
    from test_replay_v2_data import source, record_decision, NOW as PERP_NOW
    from intraday.perp_bootstrap_lifecycle import start_perp_decision_soak
    from intraday.replay_v2.selection import select_rule
    store = source(tmp_path,'perp')
    start_perp_decision_soak(store,'research-rule',now=PERP_NOW)
    record_decision(store,at=PERP_NOW+timedelta(seconds=1))
    later = PERP_NOW+timedelta(days=14)
    select_rule(store,'research-rule',now=later)
    set_policy(store,'perp',enabled=True,now=later)
    return store, store.load_scoped_rule('research-rule'), later


def test_funding_retries_are_bounded_persistent_and_never_zero(tmp_path):
    store, rule, later = perp_setup(tmp_path)
    attempts = []
    def offline(*args,**kwargs):
        attempts.append(args)
        raise OSError('offline')
    for minute in (0,1,14,15,16,29,30,31,60):
        run_tick(store,now=later+timedelta(minutes=minute),report_dir=tmp_path/'reports',fetch_funding=offline)
    assert len(attempts) == 3
    assert all(args[1] == rule.created_at and args[2] == later for args in attempts)
    assert GateRepository(store).latest(rule.rule_id) is None
    state = projection(store,rule.symbol,rule.scope,now=later+timedelta(hours=1))
    assert state['next_run_at'] == (later+timedelta(minutes=30,days=7)).isoformat()
    assert state['last_job']['state'] == 'error'
    run_tick(store,now=later+timedelta(minutes=30,days=7),report_dir=tmp_path/'reports',fetch_funding=offline)
    assert len(attempts) == 4


def test_crash_after_record_reconciles_exact_gate_without_second_engine(tmp_path,monkeypatch):
    import intraday.replay_v2.automation_jobs as jobs
    store, rule, root = setup(tmp_path)
    set_policy(store,'spot',enabled=True,now=NOW)
    original = jobs._finish
    def crash(*args,**kwargs):
        raise RuntimeError('simulate process death')
    monkeypatch.setattr(jobs,'_finish',crash)
    with pytest.raises(RuntimeError):
        run_tick(store,now=NOW,report_dir=root)
    evaluation = GateRepository(store).latest(rule.rule_id)
    assert evaluation.status == 'pass'
    monkeypatch.setattr(jobs,'_finish',original)
    def duplicate(*args,**kwargs):
        pytest.fail('duplicate engine after recorded pass')
    monkeypatch.setattr(jobs,'replay_gate',duplicate)
    run_tick(store,now=NOW+timedelta(minutes=1),report_dir=root)
    state = projection(store,rule.symbol,rule.scope,now=NOW+timedelta(minutes=1))
    assert state['status'] == 'passed_waiting_operator'
    with store._connect() as c:
        assert c.execute('SELECT count(*) FROM replay_gate_evaluations').fetchone()[0] == 1
        assert c.execute('SELECT state FROM gate_automation_jobs').fetchone()[0] == 'done'


def test_selected_policy_blocks_legacy_even_when_paused(tmp_path):
    from intraday.replay_v2.compatibility import require_legacy_campaign
    store, rule, root = setup(tmp_path)
    set_policy(store,'spot',enabled=True,now=NOW)
    set_policy(store,'spot',enabled=False,now=NOW)
    with pytest.raises(ValueError,match='v2'):
        require_legacy_campaign(store,rule.rule_id)


def test_readiness_projects_weekly_schedule_without_writes(tmp_path):
    from intraday.asset_readiness import build_asset_readiness
    store,rule,root = setup(tmp_path)
    set_policy(store,'spot',enabled=True,now=NOW)
    before = dump(store.database)
    row = build_asset_readiness(store.database,symbol='ETH',market='spot',now=NOW)['rows'][0]
    assert row['automation']['status'] == 'due'
    assert row['gate_version'] == 'gate-v2.1'
    assert dump(store.database) == before


def test_nonpass_crash_does_not_rerun_before_weekly_due(tmp_path,monkeypatch):
    import intraday.replay_v2.automation_jobs as jobs
    store,rule,root = setup(tmp_path)
    replay = replay_gate(store,rule.rule_id,now=NOW,report_dir=root)
    start_gate_soak(store,rule.rule_id,evaluation_id=replay.evaluation_id,now=NOW,report_dir=root)
    set_policy(store,'spot',enabled=True,now=NOW)
    original = jobs._finish
    monkeypatch.setattr(jobs,'_finish',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('death')))
    later = NOW+timedelta(days=14)
    with pytest.raises(RuntimeError):
        run_tick(store,now=later,report_dir=root)
    monkeypatch.setattr(jobs,'_finish',original)
    monkeypatch.setattr(jobs,'evaluate_gate_soak',lambda *a,**kw:pytest.fail('nonpass rerun before 7 days'))
    run_tick(store,now=later+timedelta(minutes=1),report_dir=root)
    with store._connect() as c:
        assert c.execute('SELECT count(*) FROM replay_gate_evaluations').fetchone()[0] == 2


def test_future_perp_baseline_starts_collection_without_legacy_replay(tmp_path,monkeypatch):
    from intraday.__main__ import _run_asset_rule_bootstrap_tick
    from intraday.store import IntradayStore
    store = IntradayStore(tmp_path/'source.sqlite')
    set_policy(store,'all',enabled=True,now=NOW)
    store.register_asset('DOGE',market='perp',now=NOW)
    from dataclasses import replace
    spec = replace(store.asset_spec('DOGE'),binance_perp_symbol='DOGEUSDT')
    monkeypatch.setattr(store,'asset_catalog',lambda:{'DOGEUSDT':spec})
    monkeypatch.setattr('intraday.__main__.start_perp_decision_soak',lambda *a,**kw:pytest.fail('legacy collection guard'))
    monkeypatch.setattr('intraday.__main__.replay_perp_bootstrap',lambda *a,**kw:pytest.fail('legacy replay'))
    _run_asset_rule_bootstrap_tick(store,now=NOW)
    registry = store.scoped_rule_registry(DecisionScope.PERP_INTRADAY,symbol='DOGEUSDT')
    assert registry['challenger_id'] == 'dogeusdt-perp-baseline-v1'
    assert registry['updated_at'] == NOW.isoformat()
    assert store.asset_lifecycle('DOGEUSDT',DecisionScope.PERP_INTRADAY).stage.value == 'soak'
    _run_asset_rule_bootstrap_tick(store,now=NOW+timedelta(days=1))
    assert store.scoped_rule_registry(DecisionScope.PERP_INTRADAY,symbol='DOGEUSDT')['updated_at'] == NOW.isoformat()


def test_perp_weekly_job_uses_real_engine_funding_and_original_anchor(tmp_path):
    from test_replay_v2_data import source, NOW as PERP_NOW
    from test_replay_gate_perp_e2e import phase
    from intraday.perp_bootstrap_lifecycle import start_perp_decision_soak
    from intraday.replay_v2.selection import select_rule
    from intraday.replay_v2.funding import read_funding_snapshot
    store = source(tmp_path,'perp')
    start_perp_decision_soak(store,'research-rule',now=PERP_NOW)
    root = tmp_path/'reports'
    end, funding_id = phase(store,PERP_NOW,root)
    registry = store.scoped_rule_registry(DecisionScope.PERP_INTRADAY,symbol='DOGEUSDT')
    select_rule(store,'research-rule',now=end)
    set_policy(store,'all',enabled=True,now=end)
    fetched = []
    def funding(symbol,start,cutoff,**kwargs):
        fetched.append((symbol,start,cutoff))
        return read_funding_snapshot(root,funding_id)
    result = run_tick(store,now=end,report_dir=root,fetch_funding=funding)
    assert result['DOGEUSDT:perp']['status'] == 'pass', result
    assert fetched == [('DOGEUSDT',PERP_NOW,end)]
    assert GateRepository(store).current('DOGEUSDT',DecisionScope.PERP_INTRADAY) is None
    assert store.scoped_rule_registry(DecisionScope.PERP_INTRADAY,symbol='DOGEUSDT') == registry


def test_hard_risk_halts_before_minimum_window(tmp_path):
    store,rule,later = perp_setup(tmp_path)
    store.record_portfolio_soak_tick(symbol=rule.symbol,scope=rule.scope,status='success',
        hard_risk_violation=True,created_at=rule.created_at+timedelta(days=1))
    state = projection(store,rule.symbol,rule.scope,now=later)
    assert state['status'] == 'halted'
    assert 'hard_risk_violation' in state['blockers']
    assert run_tick(store,now=later,report_dir=tmp_path/'reports') == {}


def test_changed_prefix_halts_durable_job_without_evaluation(tmp_path):
    store,rule,later = perp_setup(tmp_path)
    with store._connect() as c:
        c.execute("UPDATE model_calls SET status='error'")
    result = run_tick(store,now=later,report_dir=tmp_path/'reports')
    assert result[rule.symbol+':perp']['status'] == 'halted'
    assert GateRepository(store).latest(rule.rule_id) is None
    assert projection(store,rule.symbol,rule.scope,now=later)['status'] == 'halted'


def test_process_lock_blocks_second_worker_and_manual_gate(tmp_path):
    from intraday.replay_v2.automation_lock import gate_lock
    store,rule,root = setup(tmp_path)
    set_policy(store,'spot',enabled=True,now=NOW)
    with gate_lock(store.database):
        assert run_tick(store,now=NOW,report_dir=root) == {'status':'busy'}
        with pytest.raises(ValueError,match='already running'):
            with gate_lock(store.database):
                pytest.fail('second lock acquired')
    assert GateRepository(store).latest(rule.rule_id) is None


def test_v1_rejected_spot_is_audited_by_v2_without_rewriting_old_status(tmp_path):
    store,rule,root = setup(tmp_path)
    store.update_scoped_rule_status(rule.rule_id,expected='queued',status='rejected')
    set_policy(store,'spot',enabled=True,now=NOW)
    assert projection(store,rule.symbol,rule.scope,now=NOW)['status'] == 'due'
    result = run_tick(store,now=NOW,report_dir=root)
    assert result[rule.symbol+':spot']['status'] == 'pass'
    assert store.scoped_rule_status(rule.rule_id) == 'rejected'
    assert store.latest_scoped_rule_evaluation(rule.rule_id,kind='replay') is None


def test_old_btc_demo_activation_cannot_bypass_selected_policy(tmp_path):
    from test_execution_source import source_fixture as btc_source, NOW as SOURCE_NOW
    from intraday.execution.source import EvidenceSource
    from intraday.execution.scoped_source import ScopedEvidenceSource
    from intraday.store import IntradayStore
    path,evaluation_id = btc_source(tmp_path),'0123456789abcdef'
    store = IntradayStore(path)
    set_policy(store,'perp',enabled=True,now=SOURCE_NOW)
    with pytest.raises(ValueError,match='v2'):
        EvidenceSource(path).evaluation(evaluation_id,now=SOURCE_NOW)
    with store._connect() as c:
        c.execute('INSERT INTO asset_venue_routes VALUES (?,?,?,?,?,?,?)',
                  ('BTCUSDT','perp','bnb','demo','BTCUSDT','fixture',SOURCE_NOW.isoformat()))
    with pytest.raises(ValueError,match='v2'):
        ScopedEvidenceSource(path,symbol='BTC',market='perp').evaluation(evaluation_id,now=SOURCE_NOW)


@pytest.mark.parametrize('entrypoint',['evaluate_scoped_replay','start_scoped_rule_soak','evaluate_scoped_soak','activate_scoped_rule'])
def test_generic_legacy_lifecycle_cannot_bypass_v2_policy(tmp_path,entrypoint):
    import intraday.scoped_rule_lifecycle as legacy
    store,rule,root = setup(tmp_path)
    set_policy(store,'spot',enabled=True,now=NOW)
    kwargs = {'evaluation_id':'old-v1-id'} if entrypoint == 'activate_scoped_rule' else {}
    with pytest.raises(ValueError,match='v2'):
        getattr(legacy,entrypoint)(store,rule.rule_id,now=NOW,**kwargs)


def test_legacy_daily_proposals_do_not_auto_tune_selected_perp(tmp_path,monkeypatch):
    import intraday.runtime as runtime
    store,rule,root = setup(tmp_path)
    set_policy(store,'perp',enabled=True,now=NOW)
    monkeypatch.setattr(runtime,'active_llm_client',lambda *a,**kw:object())
    monkeypatch.setattr(store,'latest_market_thesis_bundle',lambda:object())
    monkeypatch.setattr(store,'latest_retrospective',lambda:object())
    monkeypatch.setattr(runtime,'should_generate_scoped_rule',lambda *a,scope,**kw:scope is DecisionScope.PERP_INTRADAY)
    result = runtime.run_rule_proposal_cycle(store,object(),now=NOW)
    assert result == {'status':'skipped','reason':'no_eligible_scope'}


def test_excluded_legacy_scopes_are_not_seeded_by_proposal_pipeline(tmp_path):
    from intraday.llm_pipeline import LLMAnalysisPipeline
    store,rule,root = setup(tmp_path)
    before = dump(store.database)
    assert LLMAnalysisPipeline(object(),store).generate_scoped_candidates(object(),now=NOW,generate_scopes=set()) == []
    assert dump(store.database) == before
