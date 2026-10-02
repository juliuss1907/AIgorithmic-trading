"""Explicit v2 choice, independent of rule hashes and validation campaign clocks."""
from datetime import datetime, timedelta
from pydantic import field_validator

from intraday.contracts import DecisionScope, ScopedRuleCandidate
from intraday.replay_v2.contracts import FrozenModel, ReplayConfig, utc
from intraday.replay_v2.metrics import encoded, fingerprint, dataset_fingerprint


class GateSelection(FrozenModel):
    candidate_id: str
    symbol: str
    scope: DecisionScope
    rule_hash: str
    selected_at: datetime
    collection_started_at: datetime | None = None
    prefix_config: ReplayConfig | None = None
    prefix_checksum: str | None = None

    @field_validator('selected_at', 'collection_started_at')
    @classmethod
    def aware_time(cls, value):
        return utc(value) if value is not None else None


def read_selection(store, candidate_id):
    with store._connect() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='gate_v2_selections' AND type='table'").fetchone():
            return None
        row = c.execute('SELECT payload_json,checksum FROM gate_v2_selections WHERE candidate_id=?', (candidate_id,)).fetchone()
    if row is None:
        return None
    value = GateSelection.model_validate_json(row['payload_json'])
    if value.candidate_id != candidate_id or fingerprint(value.model_dump(mode='json')) != row['checksum']:
        raise ValueError('v2 selection checksum mismatch')
    return value


def route_selected(store, symbol, scope):
    with store._connect() as c:
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='gate_v2_selections' AND type='table'").fetchone()
        return bool(exists and c.execute('SELECT 1 FROM gate_v2_selections WHERE symbol=? AND scope=?', (symbol, scope.value)).fetchone())


def verify_selection(store, rule, selection, *, now, audit_prefix=False):
    if (rule is None or rule.rule_id != selection.candidate_id or rule.content_hash != selection.rule_hash
            or rule.symbol != selection.symbol or rule.scope != selection.scope or selection.selected_at > utc(now)):
        raise ValueError('v2 selection rule binding mismatch')
    if audit_prefix and selection.prefix_config:
        from intraday.replay_v2.data import load_dataset
        data = load_dataset(store.database, selection.prefix_config, reader=store if store.read_only else None)
        if dataset_fingerprint(data) != selection.prefix_checksum:
            raise ValueError('v2 collection evidence changed since selection')


def select_rule(store, candidate_id, *, now, dry_run=False):
    from intraday.replay_v2.collection import read_collection_binding
    from intraday.replay_v2.data import load_dataset
    now = utc(now)
    rule = store.load_scoped_rule(candidate_id)
    existing = read_selection(store, candidate_id)
    if existing:
        verify_selection(store, rule, existing, now=now, audit_prefix=True)
        return existing
    if rule is None or rule.scope not in {DecisionScope.SPOT_4H, DecisionScope.PERP_INTRADAY}:
        raise ValueError('v2 selection requires Spot 4h or Perp candidate')
    if rule.created_at > now:
        raise ValueError('cannot select a future rule')
    if rule.scope not in store.asset_spec(rule.symbol).enabled_scopes or store.asset_lifecycle(rule.symbol, rule.scope).stage.value not in {'shadow', 'soak'}:
        raise ValueError('v2 selection requires enabled shadow/soak scope')
    registry = store.scoped_rule_registry(rule.scope, symbol=rule.symbol)
    if rule.parent_rule_id != (registry['champion_id'] or 'bootstrap') or registry['challenger_id'] not in {None, candidate_id}:
        raise ValueError('v2 selection lineage mismatch')
    active = [r for r in store.list_scoped_rules(rule.scope, symbol=rule.symbol)
              if r['status'] in {'queued','challenger','replay_passed'} and r['id'] != candidate_id]
    if active:
        raise ValueError('another candidate is active')
    start, config, checksum = None, None, None
    if rule.scope is DecisionScope.PERP_INTRADAY:
        collection = read_collection_binding(store, candidate_id)
        if collection:
            start = collection.source_config.start
        elif registry['challenger_id'] == candidate_id and registry['updated_at']:
            start = utc(datetime.fromisoformat(registry['updated_at']))
        else:
            raise ValueError('Perp decision collection must already be started')
        end = now-timedelta(minutes=15)
        if start >= end:
            raise ValueError('Perp collection has no settled evidence yet')
        config = ReplayConfig(symbol=rule.symbol, market='perp', rule_id=rule.rule_id, start=start, end=end)
        data = load_dataset(store.database, config)
        if not data.decisions or not data.quotes or any(x.startswith('unverified_recorded_decisions:') for x in data.limitations):
            raise ValueError('Perp collection provenance/quotes incomplete')
        # Model decisions may be sparse. The trusted collection anchor and actual
        # quote/heartbeat/outcome coverage are audited, not a fabricated first signal.
        checksum = dataset_fingerprint(data)
    selection = GateSelection(candidate_id=rule.rule_id, symbol=rule.symbol, scope=rule.scope,
        rule_hash=rule.content_hash, selected_at=now, collection_started_at=start,
        prefix_config=config, prefix_checksum=checksum)
    if not dry_run:
        _save(store, selection, rule, registry)
        return read_selection(store, candidate_id)
    return selection


def _save(store, selection, rule, registry):
    from intraday.replay_v2.gate_repository import GateRepository
    if store.read_only:
        raise ValueError('v2 selection requires explicit writer')
    GateRepository(store).install()
    with store._connect() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS gate_v2_selections (
                candidate_id TEXT PRIMARY KEY REFERENCES scoped_rules(id), symbol TEXT NOT NULL,
                scope TEXT NOT NULL, payload_json TEXT NOT NULL, checksum TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS gate_selection_no_update BEFORE UPDATE ON gate_v2_selections
                BEGIN SELECT RAISE(ABORT,'selection is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS gate_selection_no_delete BEFORE DELETE ON gate_v2_selections
                BEGIN SELECT RAISE(ABORT,'selection is immutable'); END;
        ''')
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT payload_json FROM scoped_rules WHERE id=?', (rule.rule_id,)).fetchone()
        current = c.execute('SELECT champion_id,challenger_id,updated_at FROM asset_scoped_rule_registry WHERE symbol=? AND scope=?',
                            (rule.symbol, rule.scope.value)).fetchone()
        if not row or ScopedRuleCandidate.model_validate_json(row['payload_json']) != rule or (dict(current) if current else
            {'champion_id':None,'challenger_id':None,'updated_at':None}) != {k:registry.get(k) for k in ('champion_id','challenger_id','updated_at')}:
            raise ValueError('rule/registry changed during v2 selection')
        active = c.execute("SELECT 1 FROM scoped_rules WHERE symbol=? AND scope=? AND id<>? AND status IN ('queued','challenger','replay_passed')",
                           (rule.symbol,rule.scope.value,rule.rule_id)).fetchone()
        stage = c.execute('SELECT stage FROM asset_scope_lifecycle WHERE symbol=? AND scope=?', (rule.symbol,rule.scope.value)).fetchone()
        if active or not stage or stage['stage'] not in {'shadow','soak'}:
            raise ValueError('route changed during v2 selection')
        payload = selection.model_dump(mode='json')
        old = c.execute('SELECT payload_json FROM gate_v2_selections WHERE candidate_id=?', (rule.rule_id,)).fetchone()
        if old:
            # Concurrent identical intent may have a different selection clock.
            saved = GateSelection.model_validate_json(old['payload_json'])
            if saved.rule_hash != selection.rule_hash or saved.collection_started_at != selection.collection_started_at:
                raise ValueError('v2 selection identity collision')
            return
        c.execute('INSERT INTO gate_v2_selections VALUES (?,?,?,?,?)',
                  (rule.rule_id,rule.symbol,rule.scope.value,encoded(payload),fingerprint(payload)))


def migrate_perp(store, symbols, *, now, dry_run=False):
    from intraday.replay_v2.collection import inherit_perp_collection
    from intraday.replay_v2.data import load_dataset
    rows = []
    for name in dict.fromkeys(symbols):
        try:
            symbol = store.asset_spec(name).symbol
            scope = DecisionScope.PERP_INTRADAY
            rules = store.list_scoped_rules(scope, symbol=symbol)
            active = [r for r in rules if r['status'] in {'queued','challenger','replay_passed'}]
            if len(active) > 1:
                raise ValueError('another candidate is active')
            if active:
                candidate_id = active[0]['id']
            else:
                champion = store.load_active_scoped_rule(scope, symbol=symbol)
                if champion is None:
                    raise ValueError('Perp requires an existing collection candidate or champion')
                with store._connect() as c:
                    first = c.execute("SELECT timestamp FROM signals WHERE symbol=? AND scope=? AND feature_schema_version='2' "
                        "AND decision_mode='primary' AND state_variant='numeric_v1' AND market='binance_usdm_perp' ORDER BY julianday(timestamp),id LIMIT 1",
                        (symbol,scope.value)).fetchone()
                if not first:
                    raise ValueError('Perp collection unavailable')
                start = utc(datetime.fromisoformat(first['timestamp']))
                if dry_run:
                    config = ReplayConfig(symbol=symbol,market='perp',rule_id=champion.rule_id,start=start,end=utc(now)-timedelta(minutes=15))
                    data = load_dataset(store.database,config)
                    if not data.decisions or not data.quotes or any(x.startswith('unverified_recorded_decisions:') for x in data.limitations):
                        raise ValueError('Perp collection provenance/quotes incomplete')
                    rows.append({'symbol':symbol,'status':'ready','champion_id':champion.rule_id,'collection_started_at':start.isoformat()})
                    continue
                candidate_id = inherit_perp_collection(store,symbol,collection_from=start,now=now)['candidate_id']
            selection = select_rule(store,candidate_id,now=now,dry_run=dry_run)
            rows.append({'symbol':symbol,'status':'ready' if dry_run else 'selected','selection':selection.model_dump(mode='json')})
        except (ValueError,KeyError,TypeError) as error:
            rows.append({'symbol':name,'status':'error','reason':str(error) if isinstance(error,ValueError) else 'migration data unavailable'})
    return {'dry_run':dry_run,'rows':rows,'status':'partial_failure' if any(r['status']=='error' for r in rows) else 'ok'}
