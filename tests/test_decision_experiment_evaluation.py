import json
from datetime import datetime, timedelta, timezone

from intraday.contracts import DecisionMode, DecisionScope, StateVariant
from intraday.decision_evaluation import (
    ExperimentMetrics,
    evaluate_compact_experiment,
    evaluate_eligibility,
    generate_retrospective,
)
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc)


def signal(store, *, decision_id, variant, mode, pair, direction, confidence, passed):
    return store.record_journal_signal(
        decision_id=decision_id,
        timestamp=NOW,
        symbol="BTCUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        state_snapshot=(
            '{"tokens":["scope:perp"]}'
            if variant == StateVariant.COMPACT_V1
            else '{"decision_scope":"perp_intraday"}'
        ),
        raw_signals={"price": 100.0},
        jev_answers={
            "direction": {
                "choice": direction,
                "probabilities": {direction: confidence},
            }
        },
        gate_passed=passed,
        gate_reason=None if passed else "low_confidence",
        rules_version="perp-v1",
        llm_thesis=None,
        state_variant=variant,
        decision_mode=mode,
        experiment_pair_id=pair,
    )


def outcome(store, signal_id, return_pct):
    from intraday.outcomes import SignalOutcome

    store.record_signal_outcome(SignalOutcome(
        outcome_id=f"outcome-{signal_id:016d}", signal_id=signal_id,
        horizon_sec=900, observed_at=NOW + timedelta(minutes=15),
        entry_price=100, exit_price=100 * (1 + return_pct / 100),
        forward_return_pct=return_pct,
        max_upside_pct=max(0, return_pct), max_downside_pct=min(0, return_pct),
        directional_return_pct=return_pct, mfe_pct=max(0, return_pct),
        mae_pct=min(0, return_pct), sample_count=31, coverage_pct=100,
    ))


def test_compact_evaluation_compares_paired_decisions_without_promoting(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    pair = "pair-1234567890123456"
    numeric = signal(
        store, decision_id="numeric-1", variant=StateVariant.NUMERIC_V1,
        mode=DecisionMode.PRIMARY, pair=pair, direction="Buy", confidence=0.9,
        passed=True,
    )
    compact = signal(
        store, decision_id="compact-1", variant=StateVariant.COMPACT_V1,
        mode=DecisionMode.SHADOW, pair=pair, direction="Buy", confidence=0.88,
        passed=False,
    )
    outcome(store, numeric, 1.0)
    outcome(store, compact, 1.0)

    evaluation = evaluate_compact_experiment(
        store, scope=DecisionScope.PERP_INTRADAY, evaluated_at=NOW + timedelta(days=1)
    )

    assert evaluation.status == "collecting"
    assert evaluation.metrics.paired_samples == 1
    assert evaluation.metrics.direction_agreement == 1
    assert "minimum_14_days" in evaluation.reason_codes
    assert store.latest_decision_experiment_evaluation().evaluation_id == evaluation.evaluation_id


def test_compact_eligibility_requires_every_safety_and_cost_gate():
    passing = ExperimentMetrics(
        primary_pairs=1_000, paired_samples=1_000, duration_days=14,
        availability=0.96, direction_agreement=0.9, actionable_flip_rate=0.05,
        numeric_accuracy=0.61, compact_accuracy=0.60,
        numeric_brier=0.30, compact_brier=0.31,
        numeric_latency_ms=100, compact_latency_ms=80,
        numeric_input_tokens=100, compact_input_tokens=75,
        token_reduction=0.25,
    )

    assert evaluate_eligibility(passing) == ("eligible", ())
    failing = passing.model_copy(update={"compact_brier": 0.33})
    status, reasons = evaluate_eligibility(failing)
    assert status == "reject"
    assert reasons == ("brier_regression_above_0_02",)


def test_retrospective_finds_wrong_confidence_rejected_opportunity_and_hold(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    rows = [
        ("wrong", "Buy", 0.95, True, -1.0),
        ("rejected", "Buy", 0.60, False, 1.0),
        ("hold", "Hold", 0.90, False, 1.0),
    ]
    for name, direction, confidence, passed, return_pct in rows:
        signal_id = signal(
            store, decision_id=name, variant=StateVariant.NUMERIC_V1,
            mode=DecisionMode.PRIMARY, pair=f"pair-{name:0<16}",
            direction=direction, confidence=confidence, passed=passed,
        )
        outcome(store, signal_id, return_pct)

    report = generate_retrospective(
        store, report_date=NOW.date(), generated_at=NOW + timedelta(days=1)
    )

    categories = {item["category"] for item in report["scopes"]["perp_intraday"]["examples"]}
    assert {"high_confidence_wrong", "rejected_opportunity", "hold_large_move"} <= categories
    assert store.latest_retrospective()["report_id"] == report["report_id"]
