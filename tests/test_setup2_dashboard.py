import json
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from intraday import setup2_store
from intraday.replay_v2.gate_repository import GateRepository
from intraday.setup2_dashboard import build_setup2_dashboard
from intraday.store import IntradayStore
from intraday.web import CachedSnapshot, create_app
from tests.test_learning_review import END, H4, START, kline, observe, quarters

NOW = END


@pytest.fixture
def store(tmp_path):
    store = IntradayStore(tmp_path/"intraday.sqlite3")
    setup2_store.install(store)
    GateRepository(store).install()
    trigger = START+timedelta(days=1)
    with store._connect() as c:
        for symbol, scope in (("ETHUSDT", "perp_intraday"), ("ETHUSDT", "spot_4h")):
            c.execute("INSERT INTO replay_gate_campaigns VALUES (?, ?, ?, ?, ?, 'active', '{}')",
                      (f"c-{scope}", symbol, scope, "rule-1", START.isoformat()))
        c.execute("INSERT INTO signals (decision_id, timestamp, symbol, scope, state_snapshot, raw_signals, jev_answers, "
                  "gate_passed, rules_version) VALUES ('d1', ?, 'ETHUSDT', 'perp_intraday', '{}', '{}', ?, 0, 'r')",
                  ((trigger+timedelta(seconds=4)).isoformat(),
                   json.dumps({"direction": {"choice": "Buy", "confidence": 0.36}})))
        c.execute("INSERT INTO scheduler_runs VALUES ('asset_spot_4h_hypeusdt', ?, 'error', ?, ?, 'StaleMarketData')",
                  ((NOW-timedelta(hours=1)).isoformat(),)*3)
    store.enqueue_notification("k1", "setup2_signal", "[SOAK] ETHUSDT Perp SHORT\nchi tiết", created_at=trigger)
    setup2_store.record_candles(store, "ETHUSDT", "perp", "4h", [
        kline(trigger, 100, 100.8, 98, 98.5), kline(trigger+H4, 98.5, 99, 97, 97.5)])
    quarters(store, "ETHUSDT", trigger, [100.3, 100.8]+[99.0]*14)
    observe(store, "ETHUSDT", "perp", trigger, side=-1, close=100, entry=True, donchian=True, status="called")
    observe(store, "ETHUSDT", "perp", trigger+H4, side=-1, close=98.5)
    observe(store, "ETHUSDT", "perp", trigger+2*H4, side=-1, close=97.5, exit=True)
    observe(store, "ETHUSDT", "spot", trigger, side=1, close=100, donchian=True, failed=["ema200"])
    return store


def test_read_model_joins_soak_signals_jev_hour_score_and_shadow_limit(store):
    d = build_setup2_dashboard(store, now=NOW)

    perp = next(c for c in d["campaigns"] if c["route"] == "ETHUSDT:perp")
    assert perp["bars"] == 3 and not perp["healthy"] and perp["review_at"] == (START+timedelta(days=14)).isoformat()
    [signal] = d["signals"]
    assert signal["route"] == "ETHUSDT:perp" and signal["side"] == -1
    assert signal["jev"] == dict(choice="Buy", confidence=0.36, stance="oppose")
    assert signal["simulated"]["status"] == "donchian_exit"
    assert signal["limit"]["status"] == "filled" and signal["limit"]["diff_pct"] == round((1-97.5/100.5)*100-2.5, 4)
    assert d["limit_entry"]["filled"] == 1
    spot = next(b for b in d["latest_bars"] if b["route"] == "ETHUSDT:spot")
    assert spot["breakout"] and not spot["entry"] and spot["failed_filters"] == ["ema200"]
    assert d["health"]["errors"][0]["error_code"] == "StaleMarketData" and d["health"]["stale"]
    assert d["notifications"]["recent"][0]["title"] == "[SOAK] ETHUSDT Perp SHORT"
    assert d["status"] == "attention" and any("Worker" in a for a in d["attention"])


def test_setup2_page_and_api_render(store):
    client = TestClient(create_app(database=store.database))
    page = client.get("/setup2")
    assert page.status_code == 200
    assert "Setup-2 soak" in page.text and "phản đối" in page.text and "Breakout bị lọc" in page.text
    assert "Biến thể V4" in page.text and "Thiếu ADX" in page.text      # the entry has no ADX three bars back
    api = client.get("/api/setup2").json()
    assert api["signals"][0]["route"] == "ETHUSDT:perp"
    chart = client.get("/api/setup2/chart", params=dict(route="ETHUSDT:perp"))
    assert chart.status_code == 200 and chart.json()["bars"] == []            # two bars: shorter than the channel
    assert client.get("/api/setup2/chart", params=dict(route="BTCUSDT:perp")).status_code == 404


def test_setup2_page_renders_on_an_empty_database(tmp_path):
    client = TestClient(create_app(database=tmp_path/"empty.sqlite3"))
    page = client.get("/setup2")
    assert page.status_code == 200 and "Chưa có tín hiệu" in page.text


def test_cached_snapshot_serves_a_stale_copy_while_rebuilding_in_the_background():
    clock, builds = [0.0], []

    def build(now):
        builds.append(now)
        return len(builds)

    snapshot = CachedSnapshot(build, ttl_seconds=30, clock=lambda: clock[0])
    assert snapshot.get() == 1 and snapshot.get() == 1      # first call builds, the second is served from memory
    clock[0] = 31.0
    assert snapshot.get() == 1                              # stale: the old copy at once, a rebuild in the background
    for _ in range(100):
        if snapshot.value == 2 and not snapshot.refreshing:
            break
        time.sleep(0.01)
    assert snapshot.get() == 2 and len(builds) == 2


def test_cached_snapshot_rebuilds_before_answering_once_the_copy_is_too_old():
    clock, builds = [0.0], []

    def build(now):
        builds.append(now)
        if len(builds) == 3:
            raise RuntimeError("database locked")
        return len(builds)

    snapshot = CachedSnapshot(build, ttl_seconds=30, max_stale_seconds=600, clock=lambda: clock[0])
    assert snapshot.get() == 1
    clock[0] = 601.0
    assert snapshot.get() == 2                              # nobody looked for ten minutes: wait for a fresh copy
    clock[0] = 1300.0
    assert snapshot.get() == 2 and not snapshot.refreshing  # a failed rebuild still answers with the old copy


def test_price_chart_draws_candles_rule_lines_and_the_signal_stop(tmp_path):
    from intraday.setup2_dashboard import CHANNEL, chart_payload, chart_series, price_chart
    store = IntradayStore(tmp_path/"chart.sqlite3")
    setup2_store.install(store)
    first = END-60*H4
    rows = [kline(first+k*H4, 100+k*0.1, 101+k*0.1, 99+k*0.1, 100.5+k*0.1) for k in range(60)]
    setup2_store.record_candles(store, "ETHUSDT", "perp", "4h", rows)
    signal_close = first+50*H4
    observe(store, "ETHUSDT", "perp", signal_close, side=-1, close=104, entry=True, donchian=True)

    chart = price_chart(store, "ETHUSDT:perp", now=END, bars=30)

    assert len(chart["candles"]) == 30 and chart["first_at"] == (first+30*H4).isoformat()
    assert chart["legend"]["upper"] == pytest.approx(max(101+k*0.1 for k in range(60-CHANNEL-1, 59)))
    [mark] = chart["marks"]
    assert mark["side"] == -1 and mark["stop_label"] == "109.2"        # the 5% cap (109.2) is tighter than 3 ATR (110)
    assert price_chart(store, "NEARUSDT:perp", now=END)["candles"] == []

    payload = chart_payload(chart_series(store, "ETHUSDT:perp", now=END, bars=30))
    assert len(payload["bars"]) == len(payload["ema50"]) == len(payload["upper"]) == 30
    assert payload["bars"][0] == dict(at=(first+30*H4).isoformat(), open=103.0, high=104.0, low=102.0, close=103.5,
                                      volume=payload["bars"][0]["volume"])
    assert payload["marks"][0]["stop"] == pytest.approx(109.2) and payload["marks"][0]["k"] == 19   # closes bar 49
    json.dumps(payload)
