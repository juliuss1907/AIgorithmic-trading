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


def test_batch_binds_final_evidence_snapshot_not_mutable_working_database(tmp_path):
    from intraday.replay_v2.research import run_research, file_checksum
    from intraday.store import IntradayStore
    store = source(tmp_path)
    before = dump(store.database)
    result = run_research(store.database,["DOGE"],tmp_path/"reports",now=NOW,
                          markets=("spot",),collect=False)
    assert result["research_integrity"] == "ok"
    assert file_checksum(result["research_evidence_database"]) == result["research_checksum"]
    # Later diagnostics are allowed to touch the working copy, never the bound evidence.
    IntradayStore(result["snapshot"]["research_database"]).register_asset("PEPE",market="spot",now=NOW)
    assert file_checksum(result["research_evidence_database"]) == result["research_checksum"]
    assert "PEPEUSDT" not in "\n".join(dump(result["research_evidence_database"]))
    assert dump(store.database) == before


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


def test_complete_study_deduplicates_and_never_reselects_on_holdout(tmp_path, monkeypatch):
    from test_replay_v2_data import rule
    store = source(tmp_path)
    existing = rule()
    # Replace the fixture's original parameters in the isolated test database.
    existing = variant_rule(existing, {"entry_window":30,"exit_window":8}, now=NOW)
    store.register_scoped_rule(existing, status="champion")
    store.activate_scoped_champion(existing.scope, existing.rule_id, now=NOW, symbol=existing.symbol)
    start, split, end = spot_windows(NOW)
    beginning = start-timedelta(days=11)
    rows = []
    while beginning < end:
        opened = int(beginning.timestamp()*1000)
        rows.append([opened,100,101,99,100,5,opened+14_400_000-1])
        beginning += timedelta(hours=4)
    store.record_asset_candles("DOGEUSDT","4h",rows)
    before = dump(store.database)
    calls = []
    def replay(data, config, variant, root, *, now):
        calls.append((config.start, config.end, variant.parameters.entry_window))
        assert all(c.available_at <= config.end for c in data.candles)
        is_holdout = config.start == split
        rate, dd = {30:(4,2),40:(5,2),50:(6,4)}[variant.parameters.entry_window]
        return {"id":variant.rule_id,"status":"reject" if is_holdout else "pass",
            "blockers":["nonpositive_net_return"] if is_holdout else [],
            "summary":{"net_return_pct":-1 if is_holdout else rate,
                       "max_drawdown_known_pct":dd,"closed_trades":8},
            "rule":variant.model_dump(mode="json")}
    monkeypatch.setattr("intraday.replay_v2.study.run_variant",replay)
    result = study_spot(store.database,"DOGE",tmp_path/"reports",now=NOW)
    assert calls == [(start,split,30),(start,split,40),(start,split,50),(split,end,40)]
    assert result["status"] == "reject"
    assert result["blockers"] == ["nonpositive_net_return"]
    assert dump(store.database) == before
