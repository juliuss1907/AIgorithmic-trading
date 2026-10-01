from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from intraday.replay_v2.contracts import ReplayConfig, binance_gate_profile
from intraday.replay_v2.gates import GateEvidence, evaluate_report_gate, profile_fingerprint


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def report(market="spot"):
    config = ReplayConfig(symbol="DOGE", market=market, rule_id="r", start=NOW-timedelta(days=14),
                          end=NOW, profile=binance_gate_profile())
    return {"evaluator_version":"replay-v2.2", "config":config.model_dump(mode="json"),
            "status":"limited", "limitations":["rule_selected_ex_post_not_out_of_sample"],
            "summary":{"net_return_pct":2, "max_drawdown_known_pct":3,
                       "daily_expected_shortfall_known_pct":-.1, "closed_trades":6,
                       "funding_complete":True}, "events":[]}


def evidence(**overrides):
    return GateEvidence(history_bars=2190, history_coverage=1, latest_candle_age_seconds=300,
                        elapsed_days=14, outcome_count=100, outcome_coverage=1,
                        heartbeat_coverage=1, quote_coverage=1, verified_decisions=100, **overrides)


@pytest.mark.parametrize("market", ["spot", "perp"])
def test_historical_pass_is_not_claimed_to_be_oos_or_execution_approval(market):
    outcome = evaluate_report_gate(report(market), evidence())
    assert outcome.status == "pass"
    assert outcome.metrics["risk_score"] == -1


@pytest.mark.parametrize("field,value,reason", [
    ("net_return_pct",0,"nonpositive_net_return"),
    ("max_drawdown_known_pct",8,"max_drawdown_at_least_8pct"),
])
def test_gate_boundaries_are_strict(field, value, reason):
    candidate = report()
    candidate["summary"][field] = value
    outcome = evaluate_report_gate(candidate, evidence())
    assert outcome.status == "reject" and reason in outcome.reason_codes


def test_missing_funding_or_unverified_decisions_cannot_pass_with_positive_known_pnl():
    candidate = report("perp")
    candidate["summary"].update(net_return_pct=None, funding_complete=False)
    candidate["limitations"].append("unverified_recorded_decisions:1")
    outcome = evaluate_report_gate(candidate, evidence())
    assert outcome.status == "deferred"
    assert "funding_coverage_incomplete" in outcome.reason_codes
    assert "recorded_provenance_incomplete" in outcome.reason_codes


def test_hard_violation_rejects_even_when_sample_is_insufficient():
    outcome = evaluate_report_gate(report(), GateEvidence(hard_risk_violations=1))
    assert outcome.status == "reject"


def test_guard_halt_and_appreciated_exposure_are_not_hard_risk_violations():
    candidate = report()
    candidate["summary"].update(halted=True, halt_reason="daily_loss_limit", max_exposure_pct=35)
    assert evaluate_report_gate(candidate, evidence()).status == "pass"


def test_champion_uses_same_window_costs_and_daily_tail_metric():
    candidate = report()
    champion = deepcopy(candidate)
    champion["summary"].update(net_return_pct=5, max_drawdown_known_pct=1)
    assert evaluate_report_gate(candidate, evidence(), champion=champion).status == "reject"
    champion["config"]["capital"] = "2000"
    with pytest.raises(ValueError, match="same"):
        evaluate_report_gate(candidate, evidence(), champion=champion)


def test_fee_only_profile_is_not_accepted_as_the_approved_gate_profile():
    candidate = report()
    candidate["config"]["profile"].update(spot_cost_bps="10", spot_slippage_bps="0")
    with pytest.raises(ValueError, match="profile"):
        evaluate_report_gate(candidate, evidence())
    assert profile_fingerprint(binance_gate_profile()) == profile_fingerprint(binance_gate_profile(funding=None))
