"""Prepare future enabled routes without running legacy gates or proposing rules."""
from datetime import datetime, timedelta

from intraday.contracts import DecisionScope
from intraday.replay_v2.automation import MARKETS, policy
from intraday.replay_v2.gate_repository import GateRepository
from intraday.replay_v2.selection import select_rule, read_selection


def prepare_route(store, symbol, scope, *, now, spot_client):
    market = next(m for m,s in MARKETS.items() if s is scope)
    p = policy(store,market)
    if not p or not p['enabled']:
        return 'automation_paused'
    lifecycle = store.asset_lifecycle(symbol,scope)
    if lifecycle.stage.value not in {'shadow','soak'}:
        return 'route_not_collecting'
    if GateRepository(store).current(symbol,scope) or store.load_active_scoped_rule(scope,symbol=symbol):
        return 'awaiting_operator'
    rules = store.list_scoped_rules(scope,symbol=symbol)
    active = [r for r in rules if r['status'] in {'queued','challenger','replay_passed'}]
    if len(active) > 1:
        raise ValueError('ambiguous candidates')
    if not active:
        if rules:
            return 'awaiting_operator_proposal'
        if scope is DecisionScope.SPOT_4H:
            from intraday.spot_4h_lifecycle import bootstrap_spot_4h_rule
            candidate = bootstrap_spot_4h_rule(store,symbol,now=now,client=spot_client)
        else:
            from intraday.perp_bootstrap_lifecycle import bootstrap_perp_rule
            candidate = bootstrap_perp_rule(store,symbol,now=now)
    else:
        candidate = store.load_scoped_rule(active[0]['id'])
    if scope is DecisionScope.SPOT_4H:
        from intraday.spot_4h_lifecycle import refresh_spot_4h_history
        refresh_spot_4h_history(store,symbol,now=now,client=spot_client)
        select_rule(store,candidate.rule_id,now=now)
        return 'v2_history_ready'
    registry = store.scoped_rule_registry(scope,symbol=symbol)
    if registry['challenger_id'] != candidate.rule_id:
        store.start_perp_decision_challenger(candidate.rule_id,now=now)
    if lifecycle.stage.value == 'shadow':
        store.save_asset_lifecycle(lifecycle.start_soak(),updated_at=now)
    anchor = store.scoped_rule_registry(scope,symbol=symbol)['updated_at']
    if now-datetime.fromisoformat(anchor) <= timedelta(minutes=15):
        return 'v2_collection_started'
    if not read_selection(store,candidate.rule_id):
        # Preserve the original collection anchor; selection only seals its prefix.
        select_rule(store,candidate.rule_id,now=now)
    return 'v2_collection_selected'
