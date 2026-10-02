from datetime import datetime, timedelta, timezone
import sqlite3

from intraday.replay_v2.study import (
    months_before, snapshot_source, variant_rule, select_training, economic_status,
    spot_windows, study_spot,
)
from test_replay_v2_data import source, dump
from test_replay_v2_spot import inputs, bar


NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def test_calendar_split_and_leap_days():
    assert months_before(datetime(2024, 3, 31, tzinfo=timezone.utc), 1).day == 29
    start, split, end = spot_windows(NOW)
    assert (start.year, start.month) == (2024, 10)
    assert split == datetime(2026, 4, 2, tzinfo=timezone.utc)
    assert end == NOW


def test_snapshot_preserves_source_and_verified_baseline(tmp_path):
    original = source(tmp_path)
    before = dump(original.database)
    result = snapshot_source(original.database, tmp_path/"batch", now=NOW)
    assert result["integrity"] == "ok"
    assert len(result["sha256"]) == 64
    with sqlite3.connect(result["research_database"]) as c:
        c.execute("CREATE TABLE research_only (id TEXT)")
    assert dump(original.database) == before
    assert "research_only" not in "\n".join(dump(result["baseline"]))


def test_variants_change_only_explicit_parameters_and_preserve_original():
    config, data = inputs([bar(22)])
    variant = variant_rule(data.rule, {"entry_window": 30, "exit_window": 8}, now=NOW)
    assert variant.parameters.entry_window == 30
    assert variant.parameters.atr_period == data.rule.parameters.atr_period
    assert variant.parameters.jev_confidence_threshold == .85
    assert variant.content_hash != data.rule.content_hash
    assert data.rule.parameters.entry_window == 20


def item(rate, drawdown, trades=6):
    return {"id": str(rate), "summary": {"net_return_pct": rate,
            "max_drawdown_known_pct": drawdown, "closed_trades": trades},
            "status": "pass"}


def test_selection_uses_training_only_and_no_trade_result_cannot_win():
    assert select_training([item(0, 0, 0), item(4, 2), item(5, 4)])["id"] == "4"
    assert select_training([item(-1, 0), item(10, 9), item(100, 1, 0)]) is None
    assert select_training([item(4, 2), item(3, 1)])["id"] == "3"


def test_missing_spot_history_is_deferred_without_source_writes(tmp_path):
    store = source(tmp_path)
    before = dump(store.database)
    result = study_spot(store.database, "DOGE", tmp_path/"reports", now=NOW)
    assert result["status"] == "deferred"
    assert "minimum_24_month_history" in result["blockers"]
    assert dump(store.database) == before
