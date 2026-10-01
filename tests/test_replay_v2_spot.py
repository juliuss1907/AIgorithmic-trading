from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters
from intraday.replay_v2.contracts import Candle, ReplayConfig, ReplayDataset
from intraday.replay_v2.engine import simulate


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def bar(i, opening=100, high=100.1, low=99.9, closing=100):
    return Candle(opened_at=NOW+timedelta(hours=i*4), available_at=NOW+timedelta(hours=(i+1)*4),
                  open=opening, high=high, low=low, close=closing, volume=1)


def inputs(extra, *, parameters=None):
    candidate = ScopedRuleCandidate.create(rule_id="r", parent_rule_id="bootstrap", thesis_id="offline",
        symbol="ETHUSDT", scope=DecisionScope.SPOT_4H, parameters=parameters or SpotRuleParameters(),
        created_at=NOW, model_ref="operator", prompt_version="test")
    candles = tuple([bar(i) for i in range(21)] + [bar(21, 100, 110.1, 99.9, 110)] + extra)
    config = ReplayConfig(symbol="ETH", market="spot", rule_id="r",
        start=NOW+timedelta(hours=88), end=candles[-1].available_at)
    return config, ReplayDataset(rule=candidate, candles=candles)


def test_spot_uses_next_open_keeps_cash_and_never_all_in():
    config, data = inputs([bar(22, 110, 111, 109, 110)])
    report = simulate(config, data)
    entry = next(event for event in report["events"] if event["kind"] == "entry")
    assert entry["at"] == config.start.isoformat()
    assert entry["price"] == "110"
    assert Decimal(entry["quantity"])*110 == 300
    assert Decimal(entry["cash"]) == Decimal("699.55")
    assert report["summary"]["closed_trades"] == 1
    assert report["trades"][0]["exit_reason"] == "window_end"
    assert report["summary"]["net_pnl"] < 0
    assert "spot_jev_filter_not_replayed" in report["limitations"]


def test_spot_gap_guard_halts_and_never_reenters():
    config, data = inputs([bar(22, 110, 111, 109, 110), bar(23, 100, 111, 99, 110),
                           bar(24, 111, 121, 110, 120)])
    report = simulate(config, data)
    assert report["summary"]["halted"]
    assert report["trades"][0]["exit_reason"] == "daily_loss_limit"
    assert sum(event["kind"] == "entry" for event in report["events"]) == 1


def test_entry_does_not_use_future_candle_signal():
    config, data = inputs([bar(22, 110, 111, 109, 110)])
    prior = list(data.candles)
    prior[21] = bar(21)
    report = simulate(config, ReplayDataset(rule=data.rule, candles=tuple(prior)))
    assert report["summary"]["closed_trades"] == 0
    assert report["summary"]["net_pnl"] == 0


def test_spot_atr_sizing_is_applied_once():
    config, data = inputs([bar(22, 110, 111, 109, 110)])
    volatile = tuple(bar(i, 100, 120, 80, 100) for i in range(21))
    setup = bar(21, 100, 131, 90, 130)
    next_bar = bar(22, 130, 131, 129, 130)
    result = simulate(config, ReplayDataset(rule=data.rule, candles=volatile+(setup, next_bar)))
    entry = next(e for e in result["events"] if e["kind"] == "entry")
    notional = Decimal(entry["quantity"])*130
    assert 18 < notional < 20


def test_identical_inputs_are_deterministic_and_wrong_rule_is_rejected():
    config, data = inputs([bar(22, 110, 111, 109, 110)])
    assert simulate(config, data) == simulate(config, data)
    with pytest.raises(ValueError, match="identity"):
        simulate(config.model_copy(update={"symbol": "BTCUSDT"}), data)


def test_ten_percent_native_stop_is_separate_from_portfolio_loss_guard():
    config, data = inputs([bar(22, 110, 111, 109, 110)])
    volatile = tuple(bar(i, 100, 120, 80, 100) for i in range(21))
    candles = volatile+(bar(21, 100, 131, 90, 130), bar(22, 130, 131, 110, 130))
    report = simulate(config, ReplayDataset(rule=data.rule, candles=candles))
    assert report["trades"][0]["exit_reason"] == "emergency_stop"
    assert Decimal(report["trades"][0]["exit_price"]) == 117
    assert not report["summary"]["halted"]
