import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import DecisionScope, FeatureSnapshot
from intraday.outcomes import evaluate_pending_outcomes
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)


def add_snapshot(store, at, price):
    snapshot = FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=at,
        built_at=at,
        bid=price - 1,
        ask=price + 1,
        features={"price": price, "mark_price": price},
        freshness={"premium": True},
    )
    store.record_snapshot(snapshot)


def add_signal(store, *, direction="Buy", signal_time=NOW):
    return store.record_journal_signal(
        decision_id=f"decision-{direction}-{signal_time.timestamp()}",
        timestamp=signal_time,
        symbol="BTCUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        state_snapshot='{"decision_scope":"perp_intraday"}',
        raw_signals={"price": 100.0},
        jev_answers={
            "direction": {
                "choice": direction,
                "probabilities": {direction: 0.9},
            }
        },
        gate_passed=False,
        gate_reason="shadow_observation_only",
        rules_version="perp-v1",
        llm_thesis=None,
    )


def test_outcome_engine_records_forward_path_and_is_idempotent(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    signal_id = add_signal(store)
    for index in range(31):
        price = 100 + index / 10
        if index == 10:
            price = 98
        if index == 20:
            price = 105
        add_snapshot(store, NOW + timedelta(seconds=index * 30), price)

    first = evaluate_pending_outcomes(
        store, now=NOW + timedelta(minutes=15, seconds=90), horizons={900}
    )
    repeated = evaluate_pending_outcomes(
        store, now=NOW + timedelta(minutes=16), horizons={900}
    )

    assert first == {"completed": 1, "pending": 0}
    assert repeated == {"completed": 0, "pending": 0}
    outcome = store.list_signal_outcomes(signal_id=signal_id)[0]
    assert outcome.horizon_sec == 900
    assert outcome.forward_return_pct == pytest.approx(3.0)
    assert outcome.max_upside_pct == pytest.approx(5.0)
    assert outcome.max_downside_pct == pytest.approx(-2.0)
    assert outcome.directional_return_pct == pytest.approx(3.0)
    assert outcome.mfe_pct == pytest.approx(5.0)
    assert outcome.mae_pct == pytest.approx(-2.0)
    assert outcome.coverage_pct == pytest.approx(100.0)

    with sqlite3.connect(store.database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE signal_outcomes SET exit_price=1 WHERE signal_id=?",
                (signal_id,),
            )


def test_outcome_engine_leaves_low_coverage_signal_pending(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    add_signal(store)
    add_snapshot(store, NOW, 100)
    add_snapshot(store, NOW + timedelta(minutes=15), 103)

    result = evaluate_pending_outcomes(
        store, now=NOW + timedelta(minutes=20), horizons={900}
    )

    assert result == {"completed": 0, "pending": 1}
    assert store.list_signal_outcomes() == []


def test_short_outcome_signs_return_and_excursions_from_the_decision(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    signal_id = add_signal(store, direction="Sell")
    for index in range(31):
        add_snapshot(store, NOW + timedelta(seconds=index * 30), 100 - index / 10)

    evaluate_pending_outcomes(
        store, now=NOW + timedelta(minutes=17), horizons={900}
    )

    outcome = store.list_signal_outcomes(signal_id=signal_id)[0]
    assert outcome.forward_return_pct == pytest.approx(-3.0)
    assert outcome.directional_return_pct == pytest.approx(3.0)
    assert outcome.mfe_pct == pytest.approx(3.0)
    assert outcome.mae_pct == pytest.approx(0.0)
