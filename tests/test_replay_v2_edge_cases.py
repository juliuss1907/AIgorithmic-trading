from datetime import timedelta
from decimal import Decimal

from intraday.replay_v2.artifacts import publish_report, read_report
from intraday.replay_v2.contracts import ExecutionProfile, FundingHistory, QuotePoint
from intraday.replay_v2.data import load_dataset
from intraday.replay_v2.engine import simulate
from test_replay_v2_data import source, record_decision, snapshot, config, dump
from test_replay_v2_perp import inputs, quote, decision, NOW


def test_full_replay_has_no_source_mutation_network_or_model_retry(tmp_path, monkeypatch):
    store = source(tmp_path, "perp")
    record_decision(store)
    store.record_snapshot(snapshot(NOW+timedelta(seconds=30)))
    before = dump(store.database)
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay performed network IO")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    value = config("perp")
    result = simulate(value, load_dataset(store.database, value))
    saved = publish_report(tmp_path/"reports", result)
    assert read_report(tmp_path/"reports", saved["run_id"])["summary"]["closed_trades"] == 1
    assert dump(store.database) == before


def test_funding_guard_closes_only_at_next_available_quote():
    history = FundingHistory(symbol="ETH", source="synthetic", coverage_start=NOW,
        coverage_end=NOW+timedelta(seconds=201), settlements=[{"at":NOW+timedelta(seconds=20),
                                                             "rate":".1", "mark":"100"}])
    value, data = inputs([quote(0),quote(10),quote(30),quote(60)], [decision(),decision(20)],
                        profile=ExecutionProfile(funding=history))
    report = simulate(value, data)
    assert report["summary"]["halt_reason"] == "daily_loss_limit"
    assert report["trades"][0]["closed_at"] == (NOW+timedelta(seconds=30)).isoformat()
    assert len(report["trades"]) == 1


def test_stale_tail_does_not_apply_future_funding_then_backdate_forced_close():
    history = FundingHistory(symbol="ETH", source="synthetic", coverage_start=NOW,
        coverage_end=NOW+timedelta(seconds=201), settlements=[{"at":NOW+timedelta(seconds=100),
                                                             "rate":".1", "mark":"100"}])
    value, data = inputs([quote(0),quote(10),quote(160,fresh=False)], [decision()],
                        profile=ExecutionProfile(funding=history))
    report = simulate(value, data)
    assert report["summary"]["funding_paid_known"] == 0
    assert report["trades"][0]["closed_at"] == (NOW+timedelta(seconds=10)).isoformat()
    times = [event["at"] for event in report["events"]]
    assert times == sorted(times)
    assert "price_history_ends_before_window" in report["limitations"]


def test_gap_can_exceed_drawdown_limit_without_claiming_stop_guarantees():
    value, data = inputs([quote(0),quote(10),quote(30,50),quote(60)], [decision(),decision(20)])
    report = simulate(value, data)
    assert report["summary"]["max_drawdown_known_pct"] > 8
    assert report["summary"]["halt_reason"] == "max_drawdown"
    assert len(report["trades"]) == 1


def test_low_leverage_reduces_notional_for_margin_room_not_increases_pnl():
    value, data = inputs([quote(0),quote(10),quote(30,100.5)], [decision()], leverage=1)
    report = simulate(value, data)
    entry = next(event for event in report["events"] if event["kind"] == "entry")
    assert Decimal(entry["quantity"])*Decimal(entry["price"]) <= 100


def test_wide_spread_blocks_entry_even_when_decision_is_valid():
    wide = QuotePoint(at=NOW+timedelta(seconds=10), event_time=NOW+timedelta(seconds=10),
                      snapshot_id="wide",bid=99,ask=101,mark=100)
    value, data = inputs([quote(0),wide], [decision()])
    assert simulate(value, data)["summary"]["blocked_entries"] == {"spread_limit":1}
