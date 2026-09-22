import json
import sys
from datetime import datetime, timedelta, timezone

from intraday.__main__ import main
from intraday.contracts import Direction, FeatureSnapshot
from intraday.runtime import run_once
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)


def snapshot(at=NOW):
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=at,
        built_at=at,
        bid=99_990,
        ask=100_010,
        features={"price": 100_000, "mark_price": 100_000},
        freshness={"candles": True, "order_book": True},
    )


def test_doctor_reports_safe_defaults(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["intraday", "doctor", "--database", str(tmp_path / "intraday.sqlite")],
    )

    main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert result["mode"] == "paper"
    assert result["provider"] == "stub"
    assert result["execution_enabled"] is False
    assert result["leverage"] == 3
    assert result["cross_venue_mode"] == "shadow"
    assert result["hyperliquid_enabled"] is True


def test_one_stub_tick_is_persisted_without_exchange_execution(tmp_path):
    result = run_once(
        database=tmp_path / "intraday.sqlite",
        snapshot=snapshot(),
        direction=Direction.BUY,
        now=NOW,
    )

    assert result["direction"] == "Buy"
    assert result["gate"] == "authorized"
    assert result["paper_fill"] is True
    assert result["execution_enabled"] is False


def test_shadow_tick_reports_cross_venue_assessment_without_applying_it(tmp_path):
    original = snapshot()
    enriched = FeatureSnapshot.create(
        symbol=original.symbol,
        event_time=original.event_time,
        built_at=original.built_at,
        bid=original.bid,
        ask=original.ask,
        features={**original.features, "xv_coverage_score": 0},
        freshness=original.freshness,
    )

    result = run_once(
        database=tmp_path / "intraday.sqlite",
        snapshot=enriched,
        direction=Direction.BUY,
        cross_venue_mode="shadow",
        now=NOW,
    )

    assert result["cross_venue_mode"] == "shadow"
    assert result["cross_venue_status"] == "unavailable"
    assert result["cross_venue_applied"] is False


def test_pending_pause_command_is_applied_before_the_next_tick(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    store.enqueue_command("cmd-pause", "pause_entries", NOW, actor="dashboard")

    result = run_once(
        database=database,
        snapshot=snapshot(),
        direction=Direction.BUY,
        now=NOW,
    )

    assert result["gate"] == "hold"
    assert "operator_pause" in result["reasons"]
    assert store.list_commands()[0]["status"] == "applied"


def test_flatten_command_closes_paper_position_and_pauses_reentry(tmp_path):
    database = tmp_path / "intraday.sqlite"
    run_once(database=database, snapshot=snapshot(), direction=Direction.BUY, now=NOW)
    store = IntradayStore(database)
    later = NOW + timedelta(seconds=5)
    store.enqueue_command("cmd-flat", "flatten", later, actor="dashboard")

    result = run_once(
        database=database,
        snapshot=snapshot(later),
        direction=Direction.BUY,
        now=later,
    )

    assert result["tranches"] == 0
    assert result["gate"] == "hold"
    assert "operator_flatten" in result["reasons"]
    assert store.counts()["fills"] == 2
