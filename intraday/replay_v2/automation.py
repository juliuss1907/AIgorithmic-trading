"""Opt-in weekly gate policy and cheap, strictly read-only scheduling projection."""
from datetime import datetime, timedelta

from intraday.contracts import DecisionScope
from intraday.replay_v2.contracts import utc
from intraday.replay_v2.gate_repository import GateRepository
from intraday.replay_v2.selection import read_selection

MARKETS = {'spot': DecisionScope.SPOT_4H, 'perp': DecisionScope.PERP_INTRADAY}


def installed(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_automation_meta'").fetchone():
        return False
    row = connection.execute("SELECT value FROM gate_automation_meta WHERE key='version'").fetchone()
    if row is None or row['value'] != '1':
        raise ValueError('unsupported gate automation extension')
    return True


def install(store):
    if store.read_only:
        raise ValueError('automation requires explicit writer')
    with store._connect() as c:
        installed(c)
        c.executescript('''
            CREATE TABLE IF NOT EXISTS gate_automation_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT OR IGNORE INTO gate_automation_meta VALUES ('version','1');
            CREATE TABLE IF NOT EXISTS gate_automation_policy (
                market TEXT PRIMARY KEY CHECK(market IN ('spot','perp')),
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                interval_days INTEGER NOT NULL CHECK(interval_days=7), updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gate_automation_jobs (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, scope TEXT NOT NULL,
                candidate_id TEXT NOT NULL, phase TEXT NOT NULL, campaign_id TEXT,
                cutoff TEXT NOT NULL, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at TEXT, funding_id TEXT,
                evaluation_id TEXT, error_code TEXT, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS gate_auto_route ON gate_automation_jobs(symbol,scope,cutoff);
        ''')


def policy(store, market):
    with store._connect() as c:
        row = c.execute('SELECT * FROM gate_automation_policy WHERE market=?',(market,)).fetchone() if installed(c) else None
    return dict(row) if row else None


def route_requires_v2(store, symbol, scope):
    from intraday.replay_v2.selection import route_selected
    market = next((m for m,s in MARKETS.items() if s is scope),None)
    # Pause only stops scheduling. It cannot silently restore legacy admission.
    return route_selected(store,symbol,scope) or bool(market and policy(store,market))


def set_policy(store, market, *, enabled, now, interval_days=7):
    if market not in {*MARKETS,'all'} or interval_days != 7:
        raise ValueError('automation requires spot/perp/all and interval 7 days')
    markets = list(MARKETS) if market == 'all' else [market]
    existing = {m:policy(store,m) for m in markets}
    if not enabled and not any(existing.values()):
        return existing
    install(store)
    with store._connect() as c:
        for m in markets:
            if not enabled and existing[m] is None:
                continue
            c.execute('INSERT INTO gate_automation_policy VALUES (?,?,7,?) ON CONFLICT(market) '
                      'DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',
                      (m,int(enabled),utc(now).isoformat()))
    return {m:policy(store,m) for m in markets}


def last_job(store, symbol, scope, candidate_id, phase, campaign_id):
    with store._connect() as c:
        if not installed(c):
            return None
        row = c.execute('SELECT * FROM gate_automation_jobs WHERE symbol=? AND scope=? AND candidate_id=? '
            'AND phase=? AND campaign_id IS ? ORDER BY cutoff DESC,id DESC LIMIT 1',
            (symbol,scope.value,candidate_id,phase,campaign_id)).fetchone()
    return dict(row) if row else None


def projection(store, symbol, scope, *, now):
    """No DDL, network, model calls, prefix audit, or execution of a gate."""
    now = utc(now)
    market = next((m for m,s in MARKETS.items() if s is scope),None)
    p = policy(store,market) if market else None
    state = {'status':'disabled' if p is None else 'paused' if not p['enabled'] else 'waiting',
             'interval_days':7,'phase':None,'candidate_id':None,'campaign_id':None,
             'window_start':None,'next_run_at':None,'last_evaluation_id':None,
             'last_evaluated_at':None,'last_result':None,'blockers':[], 'next_action':None}
    if p is None:
        return state
    campaign = GateRepository(store).current(symbol,scope)
    rules = store.list_scoped_rules(scope,symbol=symbol)
    active = [r for r in rules if r['status'] in {'queued','challenger','replay_passed'}]
    if not active and market == 'spot' and not campaign and not store.scoped_rule_registry(scope,symbol=symbol)['champion_id']:
        # A historical v1 rejection is evidence, not a v2 validation rejection.
        rejected = next((r for r in rules if r['status'] == 'rejected'),None)
        if rejected:
            active = [rejected]
    if campaign and campaign['status'] != 'active' and active and all(r['id'] != campaign['candidate_id'] for r in active):
        campaign = None
    if campaign:
        candidate_id = campaign['candidate_id']
        phase, start = 'soak', utc(datetime.fromisoformat(campaign['started_at']))
    elif len(active) == 1:
        candidate_id, phase, start = active[0]['id'], 'replay', None
    else:
        state.update(blockers=['ambiguous_candidates' if active else 'no_candidate'], next_action='prepare_or_migrate')
        return state
    rule = store.load_scoped_rule(candidate_id)
    state.update(candidate_id=candidate_id,phase=phase,campaign_id=campaign['campaign_id'] if campaign else None)
    if rule is None or rule.scope != scope or rule.symbol != symbol:
        raise ValueError('automation candidate identity mismatch')
    binding = read_selection(store,candidate_id)
    if binding and binding.rule_hash != rule.content_hash:
        raise ValueError('automation rule changed since selection')
    if market == 'perp' and phase == 'replay':
        if not binding:
            state.update(blockers=['requires_v2_collection_selection'],next_action='migrate_perp')
            return state
        start = binding.collection_started_at
    if start:
        state['window_start'] = start.isoformat()
        health = store.asset_soak_heartbeat_health(symbol,scope,started_at=start,evaluated_at=now,
            interval_seconds=30 if market == 'perp' else 14400)
        if health['hard_risk_violations']:
            state.update(status='halted',blockers=['hard_risk_violation'],next_action='investigate')
            return state
    latest = GateRepository(store).latest(candidate_id,kind=phase,campaign_id=state['campaign_id'])
    if latest:
        state.update(last_evaluation_id=latest.evaluation_id,last_evaluated_at=latest.evaluated_at.isoformat(),
                     last_result=latest.status)
        if latest.rule_content_hash != rule.content_hash:
            raise ValueError('automation evaluation rule binding mismatch')
        if latest.status == 'pass':
            if campaign and campaign['status'] == 'promoted':
                state.update(status='stopped',blockers=['campaign_promoted'])
                return state
            state.update(status='passed_waiting_operator',next_action='start_validation' if phase == 'replay' else 'review_and_promote')
            return state
        if 'hard_risk_violation' in latest.reason_codes or phase == 'soak' and latest.status == 'reject':
            state.update(status='halted',blockers=list(latest.reason_codes),next_action='investigate')
            return state
    if campaign and campaign['status'] != 'active':
        state.update(status='stopped',blockers=['campaign_'+campaign['status']])
        return state
    lifecycle = store.asset_lifecycle(symbol,scope)
    if scope not in store.asset_spec(symbol).enabled_scopes or lifecycle.stage.value not in {'shadow','soak'}:
        state.update(blockers=['route_not_collecting'],next_action='investigate')
        return state
    earliest = start+timedelta(days=14) if start else now
    if market == 'spot' and phase == 'replay':
        from intraday.spot_4h_lifecycle import spot_4h_history_progress
        history = spot_4h_history_progress(store.list_asset_candles(symbol,'4h',as_of=now),now=now)
        if history['bars'] < 2190 or history['coverage'] < .99 or history['latest_candle_age_seconds'] is None or history['latest_candle_age_seconds'] > 14700:
            state['blockers'].append('history_not_ready')
    if latest:
        earliest = max(earliest,latest.evaluated_at+timedelta(days=7))
    job = last_job(store,symbol,scope,candidate_id,phase,state['campaign_id'])
    if job:
        state['last_job'] = {k:job[k] for k in ('id','cutoff','state','attempts','error_code','evaluation_id')}
        if job['state'] == 'halted':
            state.update(status='halted',blockers=[job['error_code']],next_action='investigate')
            return state
        if job['state'] in {'running','funding_retry'}:
            earliest = utc(datetime.fromisoformat(job['retry_at'] or job['cutoff']))
        elif job['state'] == 'error':
            earliest = max(earliest,utc(datetime.fromisoformat(job['updated_at']))+timedelta(days=7))
    state['next_run_at'] = earliest.isoformat()
    if p['enabled']:
        state['status'] = 'due' if now >= earliest and not state['blockers'] else 'waiting'
    return state


def run_tick(store, *, now, report_dir=None, fetch_funding=None):
    from intraday.replay_v2.automation_jobs import run_tick as execute
    return execute(store,now=now,report_dir=report_dir,fetch_funding=fetch_funding)
