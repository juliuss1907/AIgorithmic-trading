"""Cheap source-only gate projection; never reruns account replay in a GET."""

from datetime import datetime, timedelta

from intraday.replay_v2.gate_repository import GateRepository
from intraday.replay_v2.lifecycle import perp_evidence, spot_validation_preview


def project_gate_readiness(reader, row, *, now):
    from intraday.asset_readiness import _gate
    from intraday.contracts import DecisionScope
    scope = DecisionScope(row["scope"])
    repository = GateRepository(reader)
    campaign = repository.current(row["symbol"],scope)
    replay = (repository.get(campaign["replay_evaluation_id"]) if campaign
              else repository.latest_for_route(row["symbol"],scope))
    latest_route = repository.latest_for_route(row["symbol"],scope)
    if (campaign and campaign["status"] != "active" and latest_route
            and latest_route.evaluated_at > replay.evaluated_at):
        campaign, replay = None, latest_route
    if replay is None or row["lifecycle"] == "disabled":
        return False
    rule = reader.load_scoped_rule(replay.candidate_id)
    registry = reader.scoped_rule_registry(scope,symbol=row["symbol"])
    row.update(gate_version=replay.engine_version,cost_profile=replay.replay_config.profile.model_dump(mode="json",
        exclude={"funding","instrument","instrument_observed_at","instrument_source"}),
        replay_v1=row["replay"],soak_v1=row["soak"],replay=replay.model_dump(mode="json"),
        soak=None,candidate_id=replay.candidate_id,candidate_status=reader.scoped_rule_status(replay.candidate_id),
        replay_cutoff=replay.replay_config.end.isoformat(),blockers=list(replay.reason_codes),gates=[])
    if (rule is None or rule.content_hash != replay.rule_content_hash or replay.evaluated_at > now
            or not campaign and rule.parent_rule_id != (registry["champion_id"] or "bootstrap")):
        row.update(phase="inconsistent",blockers=["v2_rule_or_evaluation_binding_mismatch"],next_action="investigate")
        return True
    if campaign and campaign["status"] == "promoted":
        saved = repository.latest(rule.rule_id,kind="soak",campaign_id=campaign["campaign_id"])
        valid = registry["champion_id"] == rule.rule_id and saved and saved.status == "pass"
        row.update(phase="champion" if valid else "inconsistent",next_action="none" if valid else "investigate",
                   blockers=[] if valid else ["v2_champion_evidence_missing"],soak=saved.model_dump(mode="json") if saved else None)
        return True
    if campaign and campaign["status"] == "rejected":
        saved = repository.latest(rule.rule_id,kind="soak",campaign_id=campaign["campaign_id"])
        row.update(phase="rejected",next_action="await_proposal",blockers=list(saved.reason_codes) if saved else ["v2_validation_rejected"],
                   soak=saved.model_dump(mode="json") if saved else None)
        return True
    if not campaign:
        metrics = replay.metrics
        row["gates"] = [_gate("closed_trades",metrics.get("closed_trades"),6,"count"),
            _gate("net_return_pct",metrics.get("net_return_pct"),0,"percent",strict=True),
            _gate("drawdown_pct",metrics.get("max_drawdown_known_pct"),8,"percent",maximum=True,strict=True)]
        row.update(phase=("awaiting_spot_soak" if row["market"] == "spot" else "awaiting_v2_validation")
                   if replay.status == "pass" else "rejected" if replay.status == "reject" else "awaiting_replay",
                   next_action="start_soak" if replay.status == "pass" else "investigate" if replay.status == "reject" else "run_replay")
        if row["market"] == "perp" and replay.status == "deferred" and registry["challenger_id"] != rule.rule_id:
            row.update(phase="awaiting_decision_soak",next_action="start_decision_soak")
        return True
    started = datetime.fromisoformat(campaign["started_at"])
    row.update(started_at=campaign["started_at"],earliest_evaluation_at=(started+timedelta(days=14)).isoformat())
    if (started > now or campaign["rule_content_hash"] != rule.content_hash or registry["challenger_id"] != rule.rule_id):
        row.update(phase="inconsistent",blockers=["v2_campaign_binding_mismatch"],next_action="investigate")
        return True
    saved = repository.latest(rule.rule_id,kind="soak",campaign_id=campaign["campaign_id"])
    row["soak"] = saved.model_dump(mode="json") if saved else None
    if row["market"] == "spot":
        outcome = spot_validation_preview(reader,rule.rule_id,campaign,now=now)
        metrics, blockers = outcome.metrics,list(outcome.reason_codes)
        row.update(phase="spot_soak",preview={"status":outcome.status,"metrics":metrics})
        row["gates"] = [_gate("matured_setups",metrics["matured_setups"],6,"count"),
                        _gate("after_cost_score",metrics["after_cost_score"],0,"percent",strict=True)]
    else:
        evidence = perp_evidence(reader,rule,started,now)
        metrics = evidence.model_dump()
        blockers = []
        if evidence.elapsed_days < 14:
            blockers.append("minimum_14_days_post_gate")
        if evidence.outcome_count < 100:
            blockers.append("minimum_100_matured_outcomes")
        if evidence.outcome_coverage < .95 or evidence.heartbeat_coverage < .95:
            blockers.append("post_gate_coverage_below_95pct")
        if evidence.hard_risk_violations:
            blockers.append("hard_risk_violation")
        row.update(phase="post_replay_validation",preview={"status":"deferred","metrics":metrics})
        row["gates"] = [_gate("matured_outcomes",metrics["outcome_count"],100,"count"),
            _gate("outcome_coverage",metrics["outcome_coverage"],.95,"fraction"),
            _gate("closed_trades",saved.metrics.get("closed_trades") if saved else None,6,"count")]
        blockers.append("v2_account_replay_evaluation_required")
    row["gates"] += [_gate("elapsed_hours",metrics["elapsed_days"]*24,336,"hours"),
        _gate("heartbeat_coverage",metrics["heartbeat_coverage"],.95,"fraction"),
        _gate("hard_risk_violations",metrics["hard_risk_violations"],0,"count",maximum=True)]
    row.update(blockers=blockers,next_action="wait" if now < started+timedelta(days=14) else "run_evaluation")
    if saved and saved.status == "pass":
        row.update(phase="awaiting_promotion",next_action="request_promotion",blockers=[])
    return True
