"""Additive weekly scheduling and pre-replay v2 preview; never legacy gate metrics."""
from datetime import timedelta

from intraday.contracts import DecisionScope
from intraday.replay_v2.automation import projection, route_requires_v2
from intraday.replay_v2.contracts import binance_gate_profile
from intraday.replay_v2.gates import GATE_VERSION
from intraday.replay_v2.lifecycle import perp_evidence
from intraday.replay_v2.selection import read_selection, verify_selection


def project_weekly_readiness(reader,row,*,now):
    from intraday.asset_readiness import _gate, PHASE_LABELS, ACTION_LABELS
    symbol,scope = row['symbol'],DecisionScope(row['scope'])
    try:
        auto = row['automation'] = projection(reader,symbol,scope,now=now)
        if not route_requires_v2(reader,symbol,scope) or row.get('gate_version') not in {None,'v1'}:
            return
        row.update(gate_version=GATE_VERSION,cost_profile=binance_gate_profile().model_dump(mode='json',
            exclude={'funding','instrument','instrument_observed_at','instrument_source'}),
            replay_v1=row['replay'],soak_v1=row['soak'],replay=None,soak=None,preview=None)
        if row['phase'] == 'inconsistent' or not row['candidate_id']:
            return
        binding = read_selection(reader,row['candidate_id'])
        rule = reader.load_scoped_rule(row['candidate_id'])
        if binding:
            verify_selection(reader,rule,binding,now=now)
        row['blockers'] = list(auto['blockers'])+['v2_account_replay_evaluation_required']
        if scope is DecisionScope.PERP_INTRADAY:
            start = binding.collection_started_at if binding else None
            if start:
                evidence = perp_evidence(reader,rule,start,now)
                earliest = start+timedelta(days=14)
                row.update(started_at=start.isoformat(),earliest_evaluation_at=earliest.isoformat(),
                    phase='decision_soak' if now < earliest else 'awaiting_replay',
                    next_action='wait' if now < earliest else 'run_replay',
                    preview={'status':'deferred','metrics':evidence.model_dump(exclude={'quote_coverage','verified_decisions'})},
                    gates=[_gate('elapsed_hours',evidence.elapsed_days*24,336,'hours'),
                           _gate('matured_outcomes',evidence.outcome_count,100,'count'),
                           _gate('outcome_coverage',evidence.outcome_coverage,.95,'fraction'),
                           _gate('heartbeat_coverage',evidence.heartbeat_coverage,.95,'fraction'),
                           _gate('hard_risk_violations',evidence.hard_risk_violations,0,'count',maximum=True)])
            else:
                row.update(phase='awaiting_decision_soak',next_action='investigate',gates=[])
        else:
            from intraday.spot_4h_lifecycle import spot_4h_history_progress
            history = spot_4h_history_progress(reader.list_asset_candles(symbol,'4h',as_of=now),now=now)
            row.update(phase='history' if history['reason_codes'] else 'awaiting_replay',
                next_action='backfill_history' if history['reason_codes'] else 'run_replay',
                gates=[_gate('history_days',history['bars']/6,365,'days'),
                       _gate('history_coverage',history['coverage'],.99,'fraction'),
                       _gate('latest_candle_age',history['latest_candle_age_seconds'],14700,'seconds',maximum=True)])
        row['phase_label'] = PHASE_LABELS[row['phase']]
        row['next_action_label'] = ACTION_LABELS[row['next_action']]
    except ValueError:
        row['automation'] = {'status':'halted','blockers':['automation_binding_invalid'],
                             'next_action':'investigate','interval_days':7}
