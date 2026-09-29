from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import DecisionScope
from intraday.contracts import ScopedRuleCandidate, SpotRuleParameters
from intraday.store import IntradayStore
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation
from intraday.spot_4h_lifecycle import (
    bootstrap_spot_4h_rule, replay_spot_4h_rule,
    start_spot_4h_soak, evaluate_spot_4h_soak,
)


NOW = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
WIDTH = 14_400_000


def trend_rows(count=2190):
    last_open = int(NOW.timestamp() * 1000) // WIDTH * WIDTH - WIDTH
    first = last_open - (count - 1) * WIDTH
    result = []
    for index in range(count):
        cycle, phase = divmod(index, 60)
        close = 100 + cycle * 5 + (phase if phase < 40 else 40 - (phase - 40) * 1.3)
        opening = first + index * WIDTH
        result.append([opening, str(close - .1), str(close + .4),
                       str(close - .4), str(close), "10", opening + WIDTH - 1])
    return result


def test_spot_4h_bootstrap_is_symbol_owned_and_replay_needs_full_year(tmp_path):
    class Client:
        def backfill(self, *, symbol, interval, start_time, end_time, now):
            assert symbol == "ETHUSDT"
            assert interval in {"4h", "8h", "1d"}
            if interval == "4h":
                return trend_rows()
            return []

    store = IntradayStore(tmp_path / "test.sqlite")
    candidate = bootstrap_spot_4h_rule(store, "ETHUSDT", now=NOW, client=Client())
    assert candidate.scope is DecisionScope.SPOT_4H
    assert candidate.parent_rule_id == "bootstrap"
    assert store.list_asset_candles("ETHUSDT", "4h") == trend_rows()
    assert store.load_active_scoped_rule(DecisionScope.SPOT_DAILY, symbol="ETHUSDT") is None
    with pytest.raises(ValueError, match="open candidate"):
        bootstrap_spot_4h_rule(store, "ETHUSDT", now=NOW, client=Client())

    evaluation = replay_spot_4h_rule(store, candidate.rule_id, now=NOW)
    assert evaluation.metrics["closed_trades"] >= 6
    assert evaluation.status == "pass"
    with pytest.raises(ValueError, match="exact passing replay"):
        start_spot_4h_soak(store, candidate.rule_id, evaluation_id="wrong", now=NOW)
    start_spot_4h_soak(store, candidate.rule_id,
                       evaluation_id=evaluation.evaluation_id, now=NOW)
    assert evaluate_spot_4h_soak(store, candidate.rule_id, now=NOW).status == "deferred"


def test_spot_4h_setup_outcome_matures_only_after_three_closed_4h_bars(tmp_path):
    store = IntradayStore(tmp_path / "test.sqlite")
    candidate = ScopedRuleCandidate.create(
        rule_id="eth-spot-4h-test", parent_rule_id="bootstrap",
        thesis_id="baseline", symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
        parameters=SpotRuleParameters(), created_at=NOW,
        model_ref="deterministic/baseline", prompt_version="test-v1",
    )
    store.register_scoped_rule(candidate)
    signal_id = store.record_journal_signal(
        decision_id="eth-4h-setup-1", timestamp=NOW, symbol="ETHUSDT",
        scope=DecisionScope.SPOT_4H, state_snapshot='{"symbol":"ETHUSDT"}',
        raw_signals={"price": 100.0},
        jev_answers={"direction": {"choice": "Buy", "probabilities": {"Buy": .9}}},
        gate_passed=False, gate_reason="asset_soak_observation_only",
        rules_version=candidate.rule_id, llm_thesis=None, market="binance_spot",
        feature_schema_version="3",
    )
    store.record_scoped_rule_soak_tick(
        candidate_id=candidate.rule_id, signal_id=signal_id,
        champion_allowed=False, challenger_allowed=True,
        champion_score=0, challenger_score=0, created_at=NOW,
    )
    first = int(NOW.timestamp() * 1000) // WIDTH * WIDTH - WIDTH
    store.record_asset_candles("ETHUSDT", "4h", [
        [first + index * WIDTH, str(100 + index), str(101 + index),
         str(99 + index), str(100 + index), "10",
         first + (index + 1) * WIDTH - 1]
        for index in range(4)
    ])
    early = store.list_scoped_rule_soak_ticks(candidate.rule_id, as_of=NOW + timedelta(hours=8))
    mature = store.list_scoped_rule_soak_ticks(candidate.rule_id, as_of=NOW + timedelta(hours=12))
    assert early[0]["directional_return_pct"] is None
    assert mature[0]["directional_return_pct"] == pytest.approx(3.0)


def test_spot_4h_soak_requires_14_days_coverage_and_six_matured_setups(tmp_path):
    from intraday.spot_4h_lifecycle import activate_spot_4h_rule

    store = IntradayStore(tmp_path / "test.sqlite")
    candidate = ScopedRuleCandidate.create(
        rule_id="eth-spot-4h-soak", parent_rule_id="bootstrap",
        thesis_id="baseline", symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
        parameters=SpotRuleParameters(), created_at=NOW,
        model_ref="deterministic/baseline", prompt_version="test-v1",
    )
    store.register_scoped_rule(candidate, status="replay_passed")
    replay = ScopedRuleEvaluation.create(
        candidate_id=candidate.rule_id, symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
        kind="replay", status="pass", evaluated_at=NOW, started_at=None,
        sample_count=8, coverage=1, champion_score=0, challenger_score=5,
    )
    store.record_scoped_rule_evaluation(replay)
    start_spot_4h_soak(store, candidate.rule_id,
                       evaluation_id=replay.evaluation_id, now=NOW)
    first = int(NOW.timestamp() * 1000) // WIDTH * WIDTH - WIDTH
    store.record_asset_candles("ETHUSDT", "4h", [
        [first + index * WIDTH, str(100 + index), str(101 + index),
         str(99 + index), str(100 + index), "10",
         first + (index + 1) * WIDTH - 1]
        for index in range(85)
    ])
    for index in range(84):
        at = NOW + timedelta(hours=4 * index)
        setup = index in {0, 12, 24, 36, 48, 60}
        store.record_portfolio_soak_tick(
            symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
            status="success" if setup else "skipped_no_setup", created_at=at,
        )
        if not setup:
            continue
        signal_id = store.record_journal_signal(
            decision_id=f"eth-4h-soak-{index}", timestamp=at, symbol="ETHUSDT",
            scope=DecisionScope.SPOT_4H, state_snapshot='{"symbol":"ETHUSDT"}',
            raw_signals={"price": float(100 + index)},
            jev_answers={"direction": {"choice": "Buy", "probabilities": {"Buy": .9}}},
            gate_passed=False, gate_reason="asset_soak_observation_only",
            rules_version=candidate.rule_id, llm_thesis=None,
            market="binance_spot", feature_schema_version="3",
        )
        store.record_scoped_rule_soak_tick(
            candidate_id=candidate.rule_id, signal_id=signal_id,
            champion_allowed=False, challenger_allowed=True,
            champion_score=0, challenger_score=0, created_at=at,
        )
    early = evaluate_spot_4h_soak(
        store, candidate.rule_id, now=NOW + timedelta(days=13, hours=23)
    )
    assert early.status == "deferred"
    passed = evaluate_spot_4h_soak(
        store, candidate.rule_id, now=NOW + timedelta(days=14)
    )
    assert passed.status == "pass"
    assert passed.sample_count == 6
    with pytest.raises(ValueError, match="exact passing soak"):
        activate_spot_4h_rule(store, candidate.rule_id,
                              evaluation_id="wrong", now=NOW + timedelta(days=14))
    activated = activate_spot_4h_rule(
        store, candidate.rule_id, evaluation_id=passed.evaluation_id,
        now=NOW + timedelta(days=14),
    )
    assert activated["champion_id"] == candidate.rule_id


def test_rejected_spot_soak_releases_slot_for_next_candidate(tmp_path):
    store = IntradayStore(tmp_path / "test.sqlite")
    candidate = ScopedRuleCandidate.create(
        rule_id="eth-spot-4h-reject", parent_rule_id="bootstrap",
        thesis_id="baseline", symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
        parameters=SpotRuleParameters(), created_at=NOW,
        model_ref="deterministic/baseline", prompt_version="test-v1",
    )
    store.register_scoped_rule(candidate, status="replay_passed")
    replay = ScopedRuleEvaluation.create(
        candidate_id=candidate.rule_id, symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
        kind="replay", status="pass", evaluated_at=NOW, started_at=None,
        sample_count=8, coverage=1, champion_score=0, challenger_score=5,
    )
    store.record_scoped_rule_evaluation(replay)
    start_spot_4h_soak(store, candidate.rule_id,
                       evaluation_id=replay.evaluation_id, now=NOW)
    store.record_portfolio_soak_tick(
        symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
        status="gate_error", hard_risk_violation=True, created_at=NOW,
    )
    evaluation = evaluate_spot_4h_soak(store, candidate.rule_id, now=NOW)
    assert evaluation.status == "reject"
    assert store.scoped_rule_status(candidate.rule_id) == "rejected"
    assert store.scoped_rule_registry(
        DecisionScope.SPOT_4H, symbol="ETHUSDT",
    )["challenger_id"] is None
