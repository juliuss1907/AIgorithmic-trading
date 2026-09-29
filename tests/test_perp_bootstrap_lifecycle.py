from datetime import datetime, timedelta, timezone
import sqlite3

from intraday.contracts import DecisionScope
from intraday.contracts import PerpRuleParameters, ScopedRuleCandidate
from intraday.outcomes import SignalOutcome
from intraday.store import IntradayStore
from intraday.perp_bootstrap_lifecycle import (
    bootstrap_perp_rule, start_perp_decision_soak,
    replay_perp_bootstrap, evaluate_perp_post_replay,
)


NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def test_perp_baseline_starts_decision_soak_before_replay(tmp_path):
    store = IntradayStore(tmp_path / "perp.sqlite")
    candidate = bootstrap_perp_rule(store, "HYPEUSDT", now=NOW)
    assert candidate.scope is DecisionScope.PERP_INTRADAY
    assert candidate.parent_rule_id == "bootstrap"
    assert store.scoped_rule_status(candidate.rule_id) == "queued"
    start_perp_decision_soak(store, candidate.rule_id, now=NOW)
    assert store.scoped_rule_status(candidate.rule_id) == "challenger"
    assert store.asset_lifecycle("HYPEUSDT", DecisionScope.PERP_INTRADAY).stage.value == "soak"
    replay = replay_perp_bootstrap(store, candidate.rule_id, now=NOW)
    assert replay.status == "deferred"
    assert "minimum_14_days" in replay.reason_codes
    assert evaluate_perp_post_replay(store, candidate.rule_id, now=NOW).status == "deferred"


def _add_outcomes(store, prefix, first, count, interval):
    answers = {
        "direction": {"choice": "Buy", "probabilities": {"Buy": .95}},
        "regime": {"choice": "Trending Up"},
        "risk_level": {"choice": "Low"},
        "toxic_flow": {"noul": .1},
        "entry_quality": {"score": 3},
    }
    for index in range(count):
        at = first + index * interval
        signal_id = store.record_journal_signal(
            decision_id=f"{prefix}-{index}", timestamp=at, symbol="ETHUSDT",
            scope=DecisionScope.PERP_INTRADAY,
            state_snapshot='{"symbol":"ETHUSDT"}',
            raw_signals={"price": 100.0}, jev_answers=answers,
            gate_passed=False, gate_reason="decision_soak_only",
            rules_version="ethusdt-perp-baseline-v1", llm_thesis=None,
        )
        store.record_signal_outcome(SignalOutcome(
            outcome_id=f"{prefix}-outcome-{index:04d}-unique",
            signal_id=signal_id, horizon_sec=900,
            observed_at=at + timedelta(minutes=15),
            entry_price=100, exit_price=101, forward_return_pct=1,
            max_upside_pct=1, max_downside_pct=0,
            directional_return_pct=1, sample_count=31, coverage_pct=100,
        ))


def _add_heartbeats(store, first, slots):
    with sqlite3.connect(store.database) as connection:
        connection.executemany(
            "INSERT INTO portfolio_soak_ticks "
            "(symbol, scope, status, hard_risk_violation, evidence_version, created_at) "
            "VALUES ('ETHUSDT', 'perp_intraday', 'success', 0, 'scope-price-v2', ?)",
            (
                ((first + timedelta(seconds=30 * index)).isoformat(),)
                for index in range(slots)
            ),
        )


def test_perp_replay_and_validation_use_nonoverlapping_evidence(tmp_path):
    store = IntradayStore(tmp_path / "perp.sqlite")
    started = NOW - timedelta(days=17)
    candidate = bootstrap_perp_rule(store, "ETHUSDT", now=started)
    start_perp_decision_soak(store, candidate.rule_id, now=started)
    _add_outcomes(store, "pre", started + timedelta(hours=1), 100,
                  timedelta(hours=3, minutes=20))
    replay_at = NOW - timedelta(hours=72)
    _add_heartbeats(store, started, 14 * 24 * 120)
    replay = replay_perp_bootstrap(store, candidate.rule_id, now=replay_at)
    assert replay.status == "pass"
    assert replay.metrics["outcomes"] == 100

    early = evaluate_perp_post_replay(store, candidate.rule_id,
                                       now=replay_at + timedelta(hours=1))
    assert early.status == "deferred"
    assert early.metrics["outcomes"] == 0

    _add_outcomes(store, "post", replay_at + timedelta(hours=1), 100,
                  timedelta(minutes=30))
    _add_heartbeats(store, replay_at, 72 * 120)
    validation = evaluate_perp_post_replay(store, candidate.rule_id, now=NOW)
    assert validation.status == "pass"
    assert validation.metrics["outcomes"] == 100


def test_rejected_perp_challenger_releases_slot_for_next_candidate(tmp_path):
    store = IntradayStore(tmp_path / "perp.sqlite")
    started = NOW - timedelta(days=15)
    candidate = bootstrap_perp_rule(store, "ETHUSDT", now=started)
    start_perp_decision_soak(store, candidate.rule_id, now=started)
    store.record_portfolio_soak_tick(
        symbol="ETHUSDT", scope=DecisionScope.PERP_INTRADAY,
        status="gate_error", hard_risk_violation=True, created_at=NOW,
    )
    evaluation = replay_perp_bootstrap(store, candidate.rule_id, now=NOW)
    assert evaluation.status == "reject"
    assert "hard_risk_violation" in evaluation.reason_codes
    assert store.scoped_rule_status(candidate.rule_id) == "rejected"
    assert store.scoped_rule_registry(
        DecisionScope.PERP_INTRADAY, symbol="ETHUSDT",
    )["challenger_id"] is None

    next_candidate = ScopedRuleCandidate.create(
        rule_id="eth-perp-candidate-v2", parent_rule_id="bootstrap",
        thesis_id="auto-proposal", symbol="ETHUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        parameters=PerpRuleParameters(), created_at=NOW + timedelta(minutes=1),
        model_ref="test/llm", prompt_version="test-v1",
    )
    store.register_scoped_rule(next_candidate)
    start_perp_decision_soak(store, next_candidate.rule_id,
                             now=NOW + timedelta(minutes=1))
    assert store.scoped_rule_registry(
        DecisionScope.PERP_INTRADAY, symbol="ETHUSDT",
    )["challenger_id"] == next_candidate.rule_id
