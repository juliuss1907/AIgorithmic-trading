"""Operator-only v2 admission and validation, independent of v1 evaluations."""

from datetime import datetime, timedelta
from math import ceil

from intraday.contracts import DecisionScope
from intraday.store import IntradayStore
from intraday.spot_4h_lifecycle import spot_4h_history_progress, preview_spot_4h_soak
from intraday.replay_v2.artifacts import publish_report, read_report, read_series, resolve_report_dir
from intraday.replay_v2.contracts import ReplayConfig, binance_gate_profile, utc
from intraday.replay_v2.data import load_dataset
from intraday.replay_v2.engine import is_setup2, simulate
from intraday.replay_v2.funding import read_funding_snapshot
from intraday.replay_v2.gate_repository import GateRepository
from intraday.replay_v2.gates import GateEvaluation, GateEvidence, GateOutcome, evaluate_report_gate, profile_fingerprint


def _candidate(reader, rule_id):
    rule = reader.load_scoped_rule(rule_id)
    if rule is None or rule.scope not in {DecisionScope.SPOT_4H,DecisionScope.PERP_INTRADAY}:
        raise ValueError("v2 gate requires a registered Spot 4h or Perp candidate")
    if rule.scope not in reader.asset_spec(rule.symbol).enabled_scopes:
        raise ValueError("candidate scope is disabled")
    registry = reader.scoped_rule_registry(rule.scope,symbol=rule.symbol)
    if rule.parent_rule_id != (registry["champion_id"] or "bootstrap"):
        raise ValueError("candidate lineage does not match current champion")
    return rule, registry


def _funding_profile(root, funding_id, *, market, symbol, start, end):
    profile = binance_gate_profile()
    if funding_id:
        if market != "perp":
            raise ValueError("funding applies only to Perp")
        history = read_funding_snapshot(root,funding_id).history
        if history.symbol != symbol or history.coverage_start > start or history.coverage_end < end:
            raise ValueError("funding snapshot does not cover this symbol/window")
        profile = profile.model_copy(update={"funding":history})
    return profile


def perp_evidence(reader, rule, start, end, data=None):
    _, summary = reader.scoped_rule_replay_evidence(rule.scope,symbol=rule.symbol,after=start,before=end)
    health = reader.asset_soak_heartbeat_health(rule.symbol,rule.scope,
        started_at=start,evaluated_at=end,interval_seconds=30)
    quotes = data.quotes if data else ()
    slots = {int((q.at-start).total_seconds()//30) for q in quotes
             if start <= q.at < end and q.fresh and 0 <= (q.at-q.event_time).total_seconds() <= 45}
    expected = max(1,ceil((end-start).total_seconds()/30))
    return GateEvidence(elapsed_days=max(0,(end-start).total_seconds()/86400),
        outcome_count=summary["outcomes"],outcome_coverage=summary["coverage"],
        heartbeat_coverage=health["coverage"],hard_risk_violations=health["hard_risk_violations"],
        quote_coverage=min(1,len(slots)/expected),verified_decisions=len(data.decisions) if data else 0)


def _setup2_inputs(reader, rule, market, now, *, campaign=None):
    """Anchored Setup-2 history: replay starts once 600 bars warm the indicators up."""
    from intraday import setup2
    from intraday.setup2_store import coin_anchor, heartbeat, list_bars
    anchor = coin_anchor(reader, rule.symbol)
    bars = list_bars(reader, rule.symbol, market, "4h", since=anchor, until=now)
    expected = int((setup2.floor_h4(now)-anchor)/setup2.H4)
    contiguous = setup2.anchored(bars, anchor) is not None
    end = bars[-1].available_at if bars else now
    start = anchor+setup2.WARMUP_BARS*setup2.H4
    if start >= end:
        start = end-setup2.H4
    values = dict(history_bars=len(bars) if contiguous else 0,
                  history_coverage=min(1.0, len(bars)/expected) if expected and contiguous else 0.0,
                  latest_candle_age_seconds=(now-bars[-1].available_at).total_seconds() if bars else None)
    if campaign:
        started = datetime.fromisoformat(campaign["started_at"])
        values.update(elapsed_days=max(0, (now-started).total_seconds()/86400),
                      heartbeat_coverage=heartbeat(reader, rule.symbol, market, rule.rule_id, start=started, end=now))
    return start, end, GateEvidence(**values)


def _replay_inputs(reader, rule, registry, now, *, root, funding_id, campaign=None):
    from intraday.replay_v2.selection import read_selection, verify_selection
    selection = read_selection(reader, rule.rule_id)
    if selection:
        verify_selection(reader, rule, selection, now=now, audit_prefix=campaign is None)
    market = "spot" if rule.scope is DecisionScope.SPOT_4H else "perp"
    collection = None
    if is_setup2(rule):
        start, end, evidence = _setup2_inputs(reader, rule, market, now, campaign=campaign)
    elif market == "spot":
        rows = reader.list_asset_candles(rule.symbol,"4h",as_of=now)
        history = spot_4h_history_progress(rows,now=now)
        end = datetime.fromtimestamp((int(rows[-1][6])+1)/1000,now.tzinfo) if rows else now
        start = (datetime.fromtimestamp(int(rows[1095][0])/1000,now.tzinfo)
                 if len(rows) > 1095 else end-timedelta(hours=4))
        evidence = GateEvidence(history_bars=history["bars"],history_coverage=history["coverage"],
                               latest_candle_age_seconds=history["latest_candle_age_seconds"])
    else:
        from intraday.replay_v2.collection import read_collection_binding, verify_collection_binding
        if not campaign:
            collection = read_collection_binding(reader, rule.rule_id)
            if collection:
                verify_collection_binding(reader, rule, collection, now=now)
        anchor = campaign["started_at"] if campaign else registry.get("updated_at")
        if collection:
            start = collection.source_config.start
        elif selection and not campaign:
            start = selection.collection_started_at
        elif not anchor or registry.get("challenger_id") != rule.rule_id:
            start = min(rule.created_at,now-timedelta(seconds=1))
        else:
            start = datetime.fromisoformat(anchor)
        if start >= now:
            start = now-timedelta(seconds=1)
        end = now
        evidence = None
    profile = _funding_profile(root,funding_id,market=market,symbol=rule.symbol,start=start,end=end)
    leverage = 1 if is_setup2(rule) else 3  # Setup-2 Perp is Isolated 1x (ADR-006).
    if campaign:
        replay = GateRepository(reader).get(campaign["replay_evaluation_id"])
        leverage = replay.replay_config.leverage
    config = ReplayConfig(symbol=rule.symbol,market=market,rule_id=rule.rule_id,
                          start=start,end=end,capital=1000,leverage=leverage,profile=profile)
    data = load_dataset(reader.database,config,reader=reader)
    if evidence is None and not is_setup2(rule):
        evidence = perp_evidence(reader,rule,start,end,data)
        if not campaign and not collection and registry.get("challenger_id") != rule.rule_id:
            evidence = evidence.model_copy(update={"elapsed_days":0,"heartbeat_coverage":0})
    return config,data,evidence


def replay_gate(store, rule_id, *, now, report_dir=None, funding_id=None, campaign=None):
    now, root = utc(now), resolve_report_dir(report_dir)
    reader = IntradayStore(store.database,read_only=True)
    with reader.read_snapshot():
        rule, registry = _candidate(reader,rule_id)
        existing = GateRepository(reader).current(rule.symbol,rule.scope)
        if existing and existing["status"] == "active" and campaign is None:
            raise ValueError("active validation must use evaluate, not restart historical replay")
        config,data,evidence = _replay_inputs(reader,rule,registry,now,root=root,funding_id=funding_id,campaign=campaign)
        report = simulate(config,data)
        if not campaign and rule.scope is DecisionScope.PERP_INTRADAY:
            from intraday.replay_v2.collection import read_collection_binding
            collection = read_collection_binding(reader, rule.rule_id)
            if collection:
                report["methodology"]["collection_inheritance"] = collection.model_dump(mode="json")
        champion_report = None
        champion = reader.load_scoped_rule(registry["champion_id"]) if registry.get("champion_id") else None
        if champion is not None and is_setup2(champion) == is_setup2(rule):
            champion_config = config.model_copy(update={"rule_id":registry["champion_id"]})
            champion_report = simulate(champion_config,load_dataset(reader.database,champion_config,reader=reader))
        outcome = evaluate_report_gate(report,evidence,champion=champion_report,kind="soak" if campaign else "replay")
    saved = publish_report(root,report,now=now)
    evaluation = GateEvaluation.create(candidate_id=rule.rule_id,rule_content_hash=rule.content_hash,
        kind="soak" if campaign else "replay",status=outcome.status,evaluated_at=now,
        campaign_id=campaign["campaign_id"] if campaign else None,
        replay_evaluation_id=campaign["replay_evaluation_id"] if campaign else None,
        replay_config=config,profile_hash=profile_fingerprint(config.profile),
        run_id=saved["run_id"],result_id=report["result_id"],dataset_checksum=report["inputs"]["dataset_checksum"],
        funding_id=funding_id,reason_codes=outcome.reason_codes,metrics=outcome.metrics)
    GateRepository(store).record(evaluation)
    return evaluation


def verify_report_binding(root,evaluation):
    if not evaluation.run_id:
        raise ValueError("gate replay report is missing")
    report = read_report(root,evaluation.run_id)
    if (report["result_id"] != evaluation.result_id or report["inputs"]["dataset_checksum"] != evaluation.dataset_checksum
            or ReplayConfig.model_validate(report["config"]) != evaluation.replay_config
            or report["inputs"]["rule_content_hash"] != evaluation.rule_content_hash):
        raise ValueError("gate replay report binding mismatch")
    for name in ("events","trades","equity_curve"):
        read_series(root,evaluation.run_id,name,limit=1)


def start_gate_soak(store, rule_id, *, evaluation_id, now, report_dir=None):
    repository = GateRepository(store)
    evaluation = repository.get(evaluation_id)
    if evaluation is None or evaluation.candidate_id != rule_id:
        raise ValueError("start-soak requires exact v2 candidate evaluation")
    verify_report_binding(resolve_report_dir(report_dir),evaluation)
    return repository.start(evaluation_id,now=now)


def spot_validation_preview(reader, rule_id, campaign, *, now):
    replay = GateRepository(reader).get(campaign["replay_evaluation_id"])
    cost = float(replay.replay_config.profile.cost_bps("spot")/100)*2
    preview = preview_spot_4h_soak(reader,rule_id,now=now,
        started_at=datetime.fromisoformat(campaign["started_at"]),round_trip_cost_pct=cost)
    metrics = {**preview.values["metrics"],"heartbeat_coverage":preview.values["coverage"],
        "after_cost_score":preview.values["challenger_score"],"hard_risk_violations":preview.hard_risk_violations,
        "elapsed_days":(now-datetime.fromisoformat(campaign["started_at"])).total_seconds()/86400}
    return GateOutcome(status=preview.values["status"],reason_codes=preview.blockers,metrics=metrics)


def evaluate_gate_soak(store, rule_id, *, now, report_dir=None, funding_id=None):
    now = utc(now)
    repository = GateRepository(store)
    rule, _ = _candidate(store,rule_id)
    campaign = repository.current(rule.symbol,rule.scope)
    if not campaign or campaign["status"] != "active" or campaign["candidate_id"] != rule_id:
        raise ValueError("candidate has no active v2 validation campaign")
    if rule.content_hash != campaign["rule_content_hash"]:
        raise ValueError("validation rule content changed")
    if rule.scope is DecisionScope.PERP_INTRADAY or is_setup2(rule):
        evaluation = replay_gate(store,rule_id,now=now,report_dir=report_dir,funding_id=funding_id,campaign=campaign)
    else:
        reader = IntradayStore(store.database,read_only=True)
        with reader.read_snapshot():
            outcome = spot_validation_preview(reader,rule_id,campaign,now=now)
        replay = repository.get(campaign["replay_evaluation_id"])
        evaluation = GateEvaluation.create(candidate_id=rule_id,rule_content_hash=rule.content_hash,
            kind="soak",status=outcome.status,evaluated_at=now,campaign_id=campaign["campaign_id"],
            replay_evaluation_id=replay.evaluation_id,replay_config=replay.replay_config,
            profile_hash=replay.profile_hash,reason_codes=outcome.reason_codes,metrics=outcome.metrics)
        repository.record(evaluation)
    if evaluation.status == "reject":
        repository.finish(evaluation.evaluation_id,now=now)
    return evaluation


def activate_gate_rule(store, rule_id, *, evaluation_id, now, report_dir=None):
    repository = GateRepository(store)
    evaluation = repository.get(evaluation_id)
    if evaluation is None or evaluation.candidate_id != rule_id:
        raise ValueError("exact v2 soak evaluation required")
    replay = repository.get(evaluation.replay_evaluation_id)
    verify_report_binding(resolve_report_dir(report_dir),replay)
    if evaluation.run_id:
        verify_report_binding(resolve_report_dir(report_dir),evaluation)
    return repository.finish(evaluation_id,now=now,promote=True)
