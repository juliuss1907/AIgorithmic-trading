"""Prevent legacy lifecycle entrypoints bypassing a selected v2 campaign."""

from intraday.replay_v2.gate_repository import GateRepository


def require_legacy_campaign(store, candidate_id):
    from intraday.replay_v2.selection import read_selection
    if read_selection(store, candidate_id):
        raise ValueError("selected candidate requires v2 evaluation; no v1 fallback")
    from intraday.replay_v2.collection import read_collection_binding
    if read_collection_binding(store, candidate_id):
        raise ValueError("inherited Perp collection requires v2 evaluation; no v1 fallback")
    candidate = store.load_scoped_rule(candidate_id)
    if candidate:
        from intraday.replay_v2.automation import route_requires_v2
        if route_requires_v2(store,candidate.symbol,candidate.scope):
            raise ValueError('selected route requires v2 evaluation; no v1 fallback')
        campaign = GateRepository(store).current(candidate.symbol,candidate.scope)
        if campaign and campaign["candidate_id"] == candidate_id:
            raise ValueError("selected v2 campaign requires v2 evaluation; no v1 fallback")


def execution_gate_evaluation(path, rule, evaluation_id, *, now):
    """Only a promoted, exact v2 campaign can satisfy manual Demo acceptance."""
    from datetime import datetime, timedelta
    from intraday.store import IntradayStore
    from intraday.replay_v2.gates import profile_fingerprint
    from intraday.replay_v2.contracts import binance_gate_profile, utc
    reader = IntradayStore(path,read_only=True)
    with reader.read_snapshot():
        repository = GateRepository(reader)
        campaign = repository.campaign_for_rule(rule.rule_id)
        evaluation = repository.get(evaluation_id)
        if campaign is None and evaluation is None:
            from intraday.replay_v2.automation import route_requires_v2
            if route_requires_v2(reader, rule.symbol, rule.scope):
                raise ValueError("selected route requires promoted v2 validation; no v1 fallback")
            from intraday.replay_v2.collection import read_collection_binding
            if read_collection_binding(reader, rule.rule_id):
                raise ValueError("inherited Perp collection requires promoted v2 validation; no v1 fallback")
            return None
        replay = repository.get(campaign["replay_evaluation_id"]) if campaign else None
        latest = repository.latest(rule.rule_id,kind="soak",campaign_id=campaign["campaign_id"]) if campaign else None
        if (not campaign or campaign["status"] != "promoted" or not evaluation or not replay or not latest
                or latest.evaluation_id != evaluation_id or evaluation.kind != "soak" or evaluation.status != "pass"
                or replay.kind != "replay" or replay.status != "pass"
                or replay.candidate_id != rule.rule_id or replay.rule_content_hash != rule.content_hash
                or replay.symbol != rule.symbol or replay.scope != rule.scope
                or replay.profile_hash != campaign["profile_hash"]
                or campaign["rule_content_hash"] != rule.content_hash
                or evaluation.replay_evaluation_id != replay.evaluation_id
                or evaluation.campaign_id != campaign["campaign_id"]
                or evaluation.candidate_id != rule.rule_id or evaluation.rule_content_hash != rule.content_hash
                or evaluation.symbol != rule.symbol or evaluation.scope != rule.scope
                or evaluation.profile_hash != campaign["profile_hash"]
                or evaluation.profile_hash != profile_fingerprint(binance_gate_profile())
                or evaluation.evaluated_at > utc(now)
                or evaluation.evaluated_at < datetime.fromisoformat(campaign["started_at"])+timedelta(days=14)):
            raise ValueError("Demo acceptance requires exact promoted v2 replay/soak binding; no v1 fallback")
        return evaluation
