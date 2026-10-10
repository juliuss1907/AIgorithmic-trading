"""Durable gate jobs. A process lock serializes engines independently of collectors."""
from datetime import datetime, timedelta
import fcntl
import os

from intraday.active_set import allows
from intraday.replay_v2.automation import MARKETS, install, policy, projection, last_job
from intraday.replay_v2.artifacts import resolve_report_dir
from intraday.replay_v2.contracts import utc
from intraday.replay_v2.gate_repository import GateRepository
from intraday.replay_v2.lifecycle import replay_gate, evaluate_gate_soak
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.selection import select_rule


def notify_pass(store, evaluation, now):
    action = 'start_validation' if evaluation.kind == 'replay' else 'review_and_promote'
    with store._connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM operator_alerts WHERE delivery_key=?',('gate-v2-pass:'+evaluation.evaluation_id,)).fetchone():
            return
        store._record_operator_alert(c,delivery_key='gate-v2-pass:'+evaluation.evaluation_id,
            kind='gate_v2_pass',severity='warning',
            message=f'{evaluation.symbol} {evaluation.scope.value}: gate v2 pass; operator {action}. Trading remains unchanged.',
            payload={'evaluation_id':evaluation.evaluation_id,'symbol':evaluation.symbol,'scope':evaluation.scope.value,
                     'phase':evaluation.kind,'next_action':action,'activation_allowed':False},created_at=now)


def _update(store, job_id, now, **values):
    allowed = {'state','attempts','retry_at','funding_id','evaluation_id','error_code'}
    if not values or set(values)-allowed:
        raise ValueError('invalid job update')
    with store._connect() as c:
        c.execute('UPDATE gate_automation_jobs SET '+','.join(k+'=?' for k in values)+',updated_at=? WHERE id=?',
                  (*values.values(),utc(now).isoformat(),job_id))


def _finish(store, job, evaluation, now):
    if evaluation.status == 'pass':
        notify_pass(store,evaluation,now)
    # A crash after record but before finish must also preserve campaign rejection.
    if evaluation.kind == 'soak' and evaluation.status == 'reject':
        campaign = GateRepository(store).current(evaluation.symbol,evaluation.scope)
        if campaign and campaign['status'] == 'active':
            GateRepository(store).finish(evaluation.evaluation_id,now=now)
    _update(store,job['id'],now,state='done',evaluation_id=evaluation.evaluation_id,retry_at=None,error_code=None)
    return evaluation.model_dump(mode='json')


def _run_route(store, symbol, scope, state, *, now, root, fetch_funding):
    phase, candidate_id, campaign_id = state['phase'], state['candidate_id'], state['campaign_id']
    job = last_job(store,symbol,scope,candidate_id,phase,campaign_id)
    if not job or job['state'] not in {'running','funding_retry'}:
        job_id = fingerprint({'symbol':symbol,'scope':scope.value,'candidate':candidate_id,
            'phase':phase,'campaign':campaign_id,'cutoff':now.isoformat()})[:32]
        with store._connect() as c:
            c.execute('INSERT OR IGNORE INTO gate_automation_jobs '
                '(id,symbol,scope,candidate_id,phase,campaign_id,cutoff,state,updated_at) VALUES (?,?,?,?,?,?,?,\'running\',?)',
                (job_id,symbol,scope.value,candidate_id,phase,campaign_id,now.isoformat(),now.isoformat()))
        job = last_job(store,symbol,scope,candidate_id,phase,campaign_id)
    cutoff = utc(datetime.fromisoformat(job['cutoff']))
    latest = GateRepository(store).latest(candidate_id,kind=phase,campaign_id=campaign_id)
    if latest and latest.evaluated_at == cutoff:
        return _finish(store,job,latest,now)
    # A later manual evaluation supersedes a crashed intent. Never append stale gates.
    if latest and latest.evaluated_at > cutoff:
        _update(store,job['id'],now,state='superseded',evaluation_id=latest.evaluation_id,retry_at=None)
        return {'status':'superseded','evaluation_id':latest.evaluation_id}
    select_rule(store,candidate_id,now=now)  # Re-audit immutable prefix after restart.
    funding_id = job['funding_id']
    if scope is MARKETS['perp'] and not funding_id:
        from intraday.replay_v2.funding import save_funding_snapshot
        if job['attempts'] >= 3:
            _update(store,job['id'],now,state='error',retry_at=None,error_code='funding_attempts_exhausted')
            return {'status':'error','attempts':job['attempts']}
        attempts = job['attempts']+1
        _update(store,job['id'],now,attempts=attempts)
        try:
            snapshot = fetch_funding(symbol,datetime.fromisoformat(state['window_start']),cutoff,now=now)
            funding_id = save_funding_snapshot(root,snapshot)['funding_id']
        except (OSError,ValueError) as error:
            retry = now+timedelta(minutes=15) if attempts < 3 else None
            _update(store,job['id'],now,state='funding_retry' if retry else 'error',
                    retry_at=retry.isoformat() if retry else None,error_code='funding_unavailable:'+type(error).__name__)
            return {'status':'funding_retry' if retry else 'error','attempts':attempts}
        _update(store,job['id'],now,funding_id=funding_id,state='running',retry_at=None)
    evaluate = replay_gate if phase == 'replay' else evaluate_gate_soak
    evaluation = evaluate(store,candidate_id,now=cutoff,report_dir=root,funding_id=funding_id)
    return _finish(store,job,evaluation,now)


def run_tick(store, *, now, report_dir=None, fetch_funding=None):
    now = utc(now)
    if not any((p := policy(store,m)) and p['enabled'] for m in MARKETS):
        return {}
    install(store)
    from intraday.replay_v2.funding import fetch_funding_snapshot
    fetch_funding = fetch_funding or fetch_funding_snapshot
    root = resolve_report_dir(report_dir)
    # OS releases flock on process death: recovery can reconcile persisted cutoff.
    lock_path = str(store.database)+'.gate-automation.lock'
    fd = os.open(lock_path,os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,0o600)
    results = {}
    try:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status':'busy'}
        for symbol,spec in store.asset_catalog().items():
            for market,scope in MARKETS.items():
                if scope not in spec.enabled_scopes or not (p := policy(store,market)) or not p['enabled']:
                    continue
                if not allows(store,symbol,scope):
                    continue  # Outside the operator active set (ADR-004): evidence kept, no weekly gate.
                key = f'{symbol}:{market}'
                state = None
                try:
                    state = projection(store,symbol,scope,now=now)
                    # Recover a recorded gate even when its pass makes projection stopped.
                    if state['last_evaluation_id']:
                        latest = GateRepository(store).get(state['last_evaluation_id'])
                        if latest.status == 'pass':
                            notify_pass(store,latest,now)
                        job = last_job(store,symbol,scope,state['candidate_id'],state['phase'],state['campaign_id'])
                        if job and job['state'] in {'running','funding_retry'} and latest.evaluated_at >= datetime.fromisoformat(job['cutoff']):
                            _finish(store,job,latest,now)
                            state = projection(store,symbol,scope,now=now)
                    if state['status'] == 'due':
                        results[key] = _run_route(store,symbol,scope,state,now=now,root=root,fetch_funding=fetch_funding)
                except (ValueError,KeyError,TypeError,OSError) as error:
                    code = 'gate_binding_error:'+type(error).__name__
                    if state and state.get('candidate_id'):
                        job = last_job(store,symbol,scope,state['candidate_id'],state['phase'],state['campaign_id'])
                        if job and job['state'] in {'running','funding_retry'}:
                            _update(store,job['id'],now,state='halted',error_code=code,retry_at=None)
                    results[key] = {'status':'halted','error_code':code}
    finally:
        os.close(fd)
    return results
