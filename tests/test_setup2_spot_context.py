"""A young Spot listing must not lose Setup-2 bars to legacy 8h/1d warm-up."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from intraday import active_set, setup2, setup2_store
from intraday.__main__ import _run_registered_asset_cycles
from intraday.config import IntradayConfig
from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
from intraday.market import StaleMarketData
from intraday.spot_signal import (
    MultiTimeframeSpotCache, SPOT_INTERVAL_MS, build_spot_multiframe_snapshot,
)
from intraday.store import IntradayStore

NOW = datetime(2026, 10, 23, 12, 1, tzinfo=timezone.utc)
END = setup2.floor_h4(NOW)
ANCHOR = END - 600 * setup2.H4
LISTED = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
GAP = datetime(2026, 10, 14, tzinfo=timezone.utc)
SYMBOL = "HYPEUSDT"
BOOK = {"bids": [["99.9", "2"]], "asks": [["100.1", "1"]]}


def rows_between(start, end, width):
    return [[at, "100", "101", "99", "100", "10", at + width - 1]
            for at in range(int(start.timestamp() * 1000), int(end.timestamp() * 1000), width)]


def candle_sets(*, short="1d"):
    result = {}
    for interval, width in SPOT_INTERVAL_MS.items():
        end_ms = int(NOW.timestamp() * 1000) // width * width
        end = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
        count = 29 if interval == short else 100
        result[interval] = [row + ["0", "0", "5"] for row in
                            rows_between(end - timedelta(milliseconds=count * width), end, width)]
    return result


def build_snapshot(sets, **kwargs):
    return build_spot_multiframe_snapshot(
        symbol=SYMBOL, candle_sets=sets, book=BOOK, event_time=NOW, built_at=NOW,
        sentiment_score=0, **kwargs,
    )


@pytest.mark.parametrize("short", ["8h", "1d"])
def test_registered_setup2_syncs_gap_and_records_heartbeat_with_short_context(
    tmp_path, monkeypatch, capsys, short,
):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    plan = active_set.slots_plan(
        {SYMBOL: "both"}, anchors={SYMBOL: ANCHOR}, warmup={SYMBOL: 400},
        proxy={SYMBOL: LISTED}, waive={SYMBOL: True},
    )
    active_set.switch(store, plan, actor="test", reason="young Spot listing", now=ANCHOR,
                      execution_check=lambda: None, initial=True)
    rule = ScopedRuleCandidate.create(
        rule_id="hype-spot-setup2", parent_rule_id="bootstrap", thesis_id="baseline",
        symbol=SYMBOL, scope=DecisionScope.SPOT_4H,
        parameters=SpotRuleParameters(entry_profile="setup2_slots_v1", entry_window=25, exit_window=10),
        created_at=ANCHOR,
        model_ref="deterministic/baseline", prompt_version="test",
    )
    store.register_scoped_rule(rule, status="replay_passed")
    store.set_scoped_challenger(rule.rule_id, now=ANCHOR)
    store.save_asset_lifecycle(store.asset_lifecycle(SYMBOL, DecisionScope.SPOT_4H).start_soak(),
                               updated_at=ANCHOR)
    setup2_store.record_candles(store, SYMBOL, "perp", "4h",
                               rows_between(ANCHOR, LISTED, 14_400_000))
    sources = {"4h": rows_between(LISTED, END, 14_400_000),
               "15m": rows_between(LISTED + timedelta(hours=3), END, 900_000)}
    for interval, rows in sources.items():
        setup2_store.record_candles(store, SYMBOL, "spot", interval,
                                   [r for r in rows if r[6] < int(GAP.timestamp() * 1000)])

    sets = candle_sets(short=short)
    # Reproduce the original cache exception with fresh H4 bars and only 29 daily bars.
    with pytest.raises(StaleMarketData, match=f"candles_{short} missing"):
        build_snapshot(sets)

    class Client:
        def candles(self, *, interval, **kwargs):
            return sets[interval]

        def order_book(self, **kwargs):
            return BOOK

    cache = MultiTimeframeSpotCache(Client())
    monkeypatch.setattr("intraday.__main__._new_asset_market_caches",
                        lambda *a, **k: ({}, {SYMBOL: cache}))
    calls = []

    def fetch(symbol, interval, start, end, *, now):
        calls.append((interval, start, end))
        return SimpleNamespace(raw_rows=[r for r in sources[interval]
                                        if int(start.timestamp() * 1000) <= r[0]
                                        and r[6] < int(end.timestamp() * 1000)])

    real_sync = setup2_store.sync
    monkeypatch.setattr(setup2_store, "sync", lambda *a, **k: real_sync(*a, **k, fetch_spot=fetch))

    class Provider:
        def decide_scoped(self, *args):
            pytest.fail("flat prices cannot create a Setup-2 entry")

    config = IntradayConfig(database=store.database)
    result = _run_registered_asset_cycles(store, Provider(), perp_markets={},
                                           spot_markets={SYMBOL: cache}, config=config, now=NOW)
    assert calls == [("4h", GAP, END), ("15m", GAP, END)]
    assert result == {f"{SYMBOL}:spot_4h": "skipped_no_setup"}
    for interval, source in sources.items():
        assert setup2_store.list_bars(store, SYMBOL, "spot", interval) == setup2.bars(
            source, setup2_store.WIDTHS[interval])
    [observation] = setup2_store.observations(store, SYMBOL, "spot", rule_id=rule.rule_id)
    assert observation["bar_close"] == END.isoformat()
    assert observation["evaluator_version"] == setup2.EVALUATOR_VERSIONS["setup2_slots_v1"]
    with store._connect() as c:
        job = c.execute("SELECT status, error_code FROM scheduler_runs WHERE job_name=?",
                        ("asset_spot_4h_hypeusdt",)).fetchone()
    assert tuple(job) == ("success", None)
    flag = f"candles_{short}_insufficient_history"
    [snapshot] = store.list_snapshots(symbol=SYMBOL, market="binance_spot")
    assert flag in snapshot.quality_flags and snapshot.freshness[f"candles_{short}"] is False
    assert flag in capsys.readouterr().out
    assert store.load_scoped_rule(rule.rule_id).parameters == rule.parameters
    assert active_set.current(store).plan == plan
    assert store.list_parent_paper_fills() == []

    # A restart in the same H4 slot and another sync cannot fabricate missing observations.
    assert _run_registered_asset_cycles(store, Provider(), perp_markets={},
                                         spot_markets={SYMBOL: cache}, config=config, now=NOW) == {}
    assert real_sync(store, SYMBOL, "spot", anchor=ANCHOR, now=NOW, fetch_spot=fetch) == {"4h": 0, "15m": 0}
    assert len(calls) == 2
    assert len(setup2_store.observations(store, SYMBOL, "spot")) == 1


@pytest.mark.parametrize("short", ["8h", "1d"])
def test_short_context_is_explicitly_missing_and_complete_snapshots_stay_identical(short):
    sets = candle_sets(short=short)
    snapshot = build_snapshot(sets, allow_short_context=True)
    assert snapshot.features[f"context_{short}_price"] is None
    assert snapshot.freshness["candles_4h"] is True
    full = candle_sets(short=None)
    assert build_snapshot(full, allow_short_context=True) == build_snapshot(full)


def test_h4_history_is_always_required():
    with pytest.raises(StaleMarketData, match="candles_4h missing"):
        build_snapshot(candle_sets(short="4h"), allow_short_context=True)


@pytest.mark.parametrize("mode,profile,expected", [
    ("active", "donchian_v1", "StaleMarketData"),
    ("inactive", "setup2_slots_v1", "StaleMarketData"),
    ("watched", "setup2_slots_v1", "RuntimeError"),
    ("active", "setup2_slots_v1", "RuntimeError"),
])
def test_only_eligible_setup2_routes_relax_context_and_sync_failures_remain_errors(
    tmp_path, monkeypatch, mode, profile, expected,
):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    plan = active_set.slots_plan(
        {SYMBOL if mode == "active" else "ETHUSDT": "both"},
        anchors={SYMBOL: ANCHOR, "ETHUSDT": ANCHOR},
        watch={SYMBOL: ANCHOR} if mode == "watched" else {},
    )
    active_set.switch(store, plan, actor="test", reason="scope isolation", now=ANCHOR,
                      execution_check=lambda: None, initial=True)
    rule = ScopedRuleCandidate.create(
        rule_id="scope-test", parent_rule_id="bootstrap", thesis_id="baseline",
        symbol=SYMBOL, scope=DecisionScope.SPOT_4H,
        parameters=SpotRuleParameters(entry_profile=profile, entry_window=25, exit_window=10),
        created_at=ANCHOR, model_ref="deterministic/baseline", prompt_version="test",
    )
    store.register_scoped_rule(rule, status="replay_passed")
    store.set_scoped_challenger(rule.rule_id, now=ANCHOR)

    class Client:
        def candles(self, *, interval, **kwargs):
            return candle_sets()[interval]

        def order_book(self, **kwargs):
            return BOOK

    cache = MultiTimeframeSpotCache(Client())
    monkeypatch.setattr("intraday.__main__._new_asset_market_caches",
                        lambda *a, **k: ({}, {SYMBOL: cache}))
    calls = []

    def failed_sync(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("history fetch failed")

    monkeypatch.setattr(setup2_store, "sync", failed_sync)
    result = _run_registered_asset_cycles(store, None, perp_markets={}, spot_markets={SYMBOL: cache},
                                           config=IntradayConfig(database=store.database), now=NOW)
    assert result == {f"{SYMBOL}:spot_4h": f"error:{expected}"}
    assert bool(calls) == (expected == "RuntimeError")
    with store._connect() as c:
        [job] = c.execute("SELECT status, error_code FROM scheduler_runs").fetchall()
    assert tuple(job) == ("error", expected)
    assert setup2_store.observations(store, SYMBOL, "spot") == []


@pytest.mark.parametrize("interval", ["4h", "8h", "1d"])
@pytest.mark.parametrize("damage", ["missing", "unclosed", "stale", "invalid"])
def test_short_context_never_relaxes_missing_stale_unclosed_or_invalid_data(interval, damage):
    sets = candle_sets(short=interval)
    width = SPOT_INTERVAL_MS[interval]
    if damage == "missing":
        sets[interval] = []
    elif damage == "unclosed":
        sets[interval][-1][6] = int(NOW.timestamp() * 1000)
    elif damage == "stale":
        for row in sets[interval]:
            row[0] -= 2 * width
            row[6] -= 2 * width
    else:
        sets[interval][0][6] -= 1
    with pytest.raises(StaleMarketData):
        build_snapshot(sets, allow_short_context=True)
