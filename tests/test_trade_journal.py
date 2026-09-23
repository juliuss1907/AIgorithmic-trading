import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import DecisionMode, DecisionScope, StateVariant
from intraday.journal import export_training_data
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc)


def signal(store, *, decision_id="decision-1", scope=DecisionScope.PERP_INTRADAY,
           direction="Buy", gate_passed=True):
    return store.record_journal_signal(
        decision_id=decision_id,
        timestamp=NOW,
        symbol="BTCUSDT",
        scope=scope,
        state_snapshot=json.dumps(
            {"decision_scope": scope.value, "features": {"rsi14": 28.3}},
            sort_keys=True,
            separators=(",", ":"),
        ),
        raw_signals={"bid": 99_990.0, "ask": 100_010.0, "rsi14": 28.3},
        jev_answers={
            "direction": {
                "choice": direction,
                "probabilities": {direction: 0.91},
            }
        },
        gate_passed=gate_passed,
        gate_reason=None if gate_passed else "low_confidence",
        rules_version="perp-champion-v3",
        llm_thesis='{"stance":"bullish"}',
    )


def trade(store, signal_id, *, trade_key="trade-1", pnl_pct=1.25,
          direction="Buy"):
    return store.record_completed_trade(
        trade_key=trade_key,
        signal_id=signal_id,
        scope=DecisionScope.PERP_INTRADAY,
        entry_fill_id=f"{trade_key}-entry",
        exit_fill_id=f"{trade_key}-exit",
        timestamp_open=NOW,
        timestamp_close=NOW + timedelta(minutes=30),
        direction=direction,
        entry_price=100_000,
        exit_price=101_300,
        stop_loss=99_000,
        take_profit=None,
        position_size=2_000,
        pnl_abs=25,
        pnl_pct=pnl_pct,
        close_reason="take_profit",
        duration_sec=1800,
        is_paper=True,
    )


def test_schema_v11_preserves_required_symbol_scope_and_paper_mode(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    signal_id = signal(store)
    trade_id = trade(store, signal_id)

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        recorded_signal = connection.execute(
            "SELECT * FROM signals WHERE id=?", (signal_id,)
        ).fetchone()
        recorded_trade = connection.execute(
            "SELECT * FROM trades WHERE id=?", (trade_id,)
        ).fetchone()

    assert version == "11"
    assert recorded_signal["symbol"] == "BTCUSDT"
    assert recorded_signal["scope"] == "perp_intraday"
    assert recorded_signal["state_variant"] == "numeric_v1"
    assert recorded_signal["decision_mode"] == "primary"
    assert recorded_signal["experiment_pair_id"] is None
    assert recorded_trade["scope"] == "perp_intraday"
    assert recorded_trade["is_paper"] == 1


def test_shadow_signal_metadata_is_persisted_without_a_trade(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")

    signal_id = store.record_journal_signal(
        decision_id="shadow-decision-1",
        timestamp=NOW,
        symbol="BTCUSDT",
        scope=DecisionScope.PERP_INTRADAY,
        state_snapshot='{"tokens":["scope:perp"]}',
        raw_signals={"price": 100_000.0},
        jev_answers={"direction": {"choice": "Buy"}},
        gate_passed=False,
        gate_reason="shadow_observation_only",
        rules_version="perp-champion-v3",
        llm_thesis=None,
        state_variant=StateVariant.COMPACT_V1,
        decision_mode=DecisionMode.SHADOW,
        experiment_pair_id="pair-1234567890123456",
    )

    with sqlite3.connect(store.database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        trades = connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert row["state_variant"] == "compact_v1"
    assert row["decision_mode"] == "shadow"
    assert row["experiment_pair_id"] == "pair-1234567890123456"
    assert trades == 0


def test_signal_and_trade_rows_are_idempotent_and_immutable(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    signal_id = signal(store)
    assert signal(store) == signal_id
    trade_id = trade(store, signal_id)
    assert trade(store, signal_id) == trade_id

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE signals SET symbol='ETHUSDT' WHERE id=?", (signal_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM trades WHERE id=?", (trade_id,))


def test_signal_rejects_non_finite_raw_numeric_values(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")

    with pytest.raises(ValueError, match="finite numeric"):
        store.record_journal_signal(
            decision_id="decision-nan",
            timestamp=NOW,
            symbol="BTCUSDT",
            scope=DecisionScope.PERP_INTRADAY,
            state_snapshot='{"decision_scope":"perp_intraday"}',
            raw_signals={"rsi14": float("nan")},
            jev_answers={"direction": {"choice": "Hold"}},
            gate_passed=False,
            gate_reason="hold",
            rules_version="perp-champion-v3",
            llm_thesis=None,
        )


def test_export_labels_winners_and_losers_and_skips_ambiguous_trades(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    winner = signal(store, decision_id="decision-win", direction="Strong Buy")
    loser = signal(store, decision_id="decision-loss", direction="Sell")
    ambiguous = signal(store, decision_id="decision-flat", direction="Buy")
    rejected = signal(
        store,
        decision_id="decision-rejected",
        direction="Buy",
        gate_passed=False,
    )
    trade(store, winner, trade_key="winner", pnl_pct=0.51, direction="Strong Buy")
    trade(store, loser, trade_key="loser", pnl_pct=-0.51, direction="Sell")
    trade(store, ambiguous, trade_key="ambiguous", pnl_pct=0.5)
    trade(store, rejected, trade_key="rejected", pnl_pct=5)

    rows = export_training_data(
        database=database,
        output_dir=tmp_path / "training_data",
        now=NOW,
    )

    assert [row["answer"]["direction"] for row in rows] == ["strong_buy", "hold"]
    assert set(rows[0]) == {"state", "questions", "answer"}
    assert rows[0]["questions"]["direction"]["type"] == "choice"
    output = tmp_path / "training_data" / "kev_finetune_20260923.jsonl"
    assert [json.loads(line) for line in output.read_text().splitlines()] == rows


@pytest.mark.parametrize(
    "minimum,maximum",
    [(0, -0.5), (0.5, 0), (-0.5, -1), (0.5, 0.5)],
)
def test_export_requires_positive_and_negative_thresholds(
    tmp_path, minimum, maximum
):
    with pytest.raises(ValueError, match="max_pnl_pct < 0 < min_pnl_pct"):
        export_training_data(
            minimum,
            maximum,
            database=tmp_path / "intraday.sqlite",
            output_dir=tmp_path / "training_data",
            now=NOW,
        )
