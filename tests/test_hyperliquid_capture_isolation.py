"""A malformed advisory feed must not stop the other assets or Binance soak."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from intraday import __main__ as cli
from intraday.assets import ASSET_REGISTRY
from intraday.cross_venue import HyperliquidFrameDataError
from intraday.contracts import (
    DecisionScope, Direction, FeatureSnapshot, JevDecision, Regime,
    RiskLevel, ScopedJevDecision, VenueMarketFrame,
)
from intraday.portfolio_soak import run_soak_cycle
from intraday.store import IntradayStore


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def frame_for(symbol):
    return VenueMarketFrame.create(
        venue="hyperliquid", symbol=symbol, event_time=NOW,
        received_at=NOW, metadata_received_at=NOW,
        bid=99.0, ask=101.0, mark_price=100.0, index_price=100.0,
        funding_bps_hour=0.0, open_interest_usd=1000.0, spread_bps=200.0,
        bid_depth_usd={k: 1.0 for k in ("5", "10", "25")},
        ask_depth_usd={k: 1.0 for k in ("5", "10", "25")},
        book_imbalance={k: 0.0 for k in ("5", "10", "25")},
    )


class Feed:
    def __init__(self, symbol, *, error=None):
        self.symbol = symbol
        self.frame = frame_for(symbol)
        self.error = error

    def latest_frame(self, *, now):
        if self.error:
            raise self.error
        return self.frame


def invalid_contract_error():
    payload = frame_for("BTCUSDT").model_dump()
    payload["checksum"] = "invalid"
    try:
        VenueMarketFrame.model_validate(payload)
    except ValidationError as error:
        return error
    raise AssertionError("invalid checksum must fail validation")


@pytest.mark.parametrize("failed_symbol", tuple(ASSET_REGISTRY))
def test_bad_coin_does_not_block_other_feeds_and_recovers(tmp_path, capsys, failed_symbol):
    store = IntradayStore(tmp_path / "source.sqlite3")
    feeds = {s: Feed(s) for s in ASSET_REGISTRY}
    feeds[failed_symbol].error = HyperliquidFrameDataError("private payload must not be logged")

    cli._record_portfolio_hyperliquid(store, feeds, now=NOW)

    actual = {
        f.symbol for s in ASSET_REGISTRY
        for f in store.list_venue_frames("hyperliquid", symbol=s)
    }
    assert actual == set(ASSET_REGISTRY) - {failed_symbol}
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["symbol"] == failed_symbol
    assert diagnostic["venue"] == "hyperliquid"
    assert diagnostic["error_code"] == "invalid_market_frame"
    assert "private payload" not in json.dumps(diagnostic)

    feeds[failed_symbol].error = None
    cli._record_portfolio_hyperliquid(store, feeds, now=NOW + timedelta(seconds=5))
    assert {
        f.symbol for s in ASSET_REGISTRY
        for f in store.list_venue_frames("hyperliquid", symbol=s)
    } == set(ASSET_REGISTRY)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("error", [HyperliquidFrameDataError("bad"), invalid_contract_error()])
def test_single_shadow_feed_data_errors_are_contained(tmp_path, capsys, error):
    store = IntradayStore(tmp_path / "source.sqlite3")
    cli._record_portfolio_hyperliquid(store, Feed("BTCUSDT", error=error), now=NOW)
    assert store.list_venue_frames("hyperliquid") == []
    assert json.loads(capsys.readouterr().out)["error_type"] == type(error).__name__


def test_database_failure_is_not_hidden():
    class BrokenStore:
        def record_venue_frame(self, frame):
            raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        cli._record_portfolio_hyperliquid(BrokenStore(), Feed("ETHUSDT"), now=NOW)


@pytest.mark.parametrize("error", [
    RuntimeError("unexpected"), KeyError("unexpected"), TypeError("unexpected"),
    ValueError("unexpected"), ZeroDivisionError("unexpected"), OverflowError("unexpected"),
])
def test_unexpected_programming_error_is_not_hidden(tmp_path, error):
    store = IntradayStore(tmp_path / "source.sqlite3")
    with pytest.raises(type(error), match="unexpected"):
        cli._record_portfolio_hyperliquid(store, Feed("ETHUSDT", error=error), now=NOW)


@pytest.mark.parametrize("error", [HyperliquidFrameDataError("bad frame"), invalid_contract_error()])
def test_active_capture_still_propagates_validation_error(capsys, error):
    with pytest.raises(type(error)):
        cli._latest_hyperliquid_frame(Feed("BTCUSDT", error=error), now=NOW, shadow=False)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("error", [HyperliquidFrameDataError("bad frame"), invalid_contract_error()])
def test_active_portfolio_capture_still_propagates_validation_error(tmp_path, error):
    store = IntradayStore(tmp_path / "source.sqlite3")
    with pytest.raises(type(error)):
        cli._record_portfolio_hyperliquid(
            store, Feed("BTCUSDT", error=error),
            now=NOW, shadow=False,
        )


def test_real_wrong_coin_feed_is_rejected_without_blocking_healthy_coin(tmp_path, capsys):
    from intraday.hyperliquid import HyperliquidFeed

    wrong = HyperliquidFeed(symbol="BTCUSDT")
    wrong.update_book({
        "coin": "ETH", "time": int(NOW.timestamp() * 1000),
        "levels": [[{"px": "99.99", "sz": "1"}], [{"px": "100.01", "sz": "1"}]],
    }, received_at=NOW)
    wrong.update_context({
        "markPx": "100", "oraclePx": "100", "funding": "0", "openInterest": "10",
    }, received_at=NOW)
    store = IntradayStore(tmp_path / "source.sqlite3")

    cli._record_portfolio_hyperliquid(store, {"BTCUSDT": wrong, "ETHUSDT": Feed("ETHUSDT")}, now=NOW)

    assert store.list_venue_frames("hyperliquid", symbol="BTCUSDT") == []
    assert len(store.list_venue_frames("hyperliquid", symbol="ETHUSDT")) == 1
    assert json.loads(capsys.readouterr().out)["symbol"] == "BTCUSDT"


@pytest.mark.parametrize("symbol", tuple(ASSET_REGISTRY))
@pytest.mark.parametrize("malformation", (
    "missing_price", "bad_quantity", "wrong_levels_shape", "crossed",
    "bad_mark", "missing_funding", "bad_time", "huge_time", "nan_funding", "negative_oi",
))
def test_real_malformed_feed_is_isolated_for_every_coin(tmp_path, capsys, symbol, malformation):
    from intraday.hyperliquid import HyperliquidFeed

    book = {
        "coin": ASSET_REGISTRY[symbol].hyperliquid_coin, "time": int(NOW.timestamp() * 1000),
        "levels": [[{"px": "99.99", "sz": "1"}], [{"px": "100.01", "sz": "1"}]],
    }
    context = {"markPx": "100", "oraclePx": "100", "funding": "0", "openInterest": "10"}
    if malformation == "missing_price":
        book["levels"][0][0].pop("px")
    elif malformation == "bad_quantity":
        book["levels"][0][0]["sz"] = "bad"
    elif malformation == "wrong_levels_shape":
        book["levels"] = {"bids": []}
    elif malformation == "crossed":
        book["levels"][0][0]["px"] = "101"
    elif malformation == "bad_mark":
        context["markPx"] = None
    elif malformation == "missing_funding":
        context.pop("funding")
    elif malformation == "bad_time":
        book["time"] = "bad"
    elif malformation == "huge_time":
        book["time"] = 1e30
    elif malformation == "nan_funding":
        context["funding"] = "NaN"
    elif malformation == "negative_oi":
        context["openInterest"] = "-1"
    bad = HyperliquidFeed(symbol=symbol)
    bad.update_book(book, received_at=NOW)
    bad.update_context(context, received_at=NOW)
    neighbor = next(s for s in ASSET_REGISTRY if s != symbol)
    store = IntradayStore(tmp_path / "source.sqlite3")

    cli._record_portfolio_hyperliquid(store, {symbol: bad, neighbor: Feed(neighbor)}, now=NOW)

    assert store.list_venue_frames("hyperliquid", symbol=symbol) == []
    assert len(store.list_venue_frames("hyperliquid", symbol=neighbor)) == 1
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["symbol"] == symbol
    assert diagnostic["error_type"] in {"HyperliquidFrameDataError", "ValidationError"}


def test_shadow_failure_does_not_prevent_real_binance_soak_tick(tmp_path):
    store = IntradayStore(tmp_path / "source.sqlite3")
    snapshot = FeatureSnapshot.create(
        symbol="BTCUSDT", event_time=NOW, built_at=NOW,
        bid=99.0, ask=101.0, features={"price": 100.0, "mark_price": 100.0},
        freshness={"candles": True, "book": True},
    )

    class Provider:
        def decide_scoped(self, market, tick_id, scope, now):
            return ScopedJevDecision(
                scope=scope, workflow="perp_intraday_entry",
                decision=JevDecision(
                    decision_id="synthetic-binance-decision", tick_id=tick_id,
                    snapshot_id=market.snapshot_id, direction=Direction.HOLD,
                    direction_confidence=0.9, regime=Regime.SIDEWAYS,
                    toxic_flow=0.1, entry_quality=4, risk_level=RiskLevel.LOW,
                    model_ref="fake/jev", created_at=now,
                ),
            )

    cli._record_portfolio_hyperliquid(store, Feed("ETHUSDT", error=HyperliquidFrameDataError("bad frame")), now=NOW)
    result = run_soak_cycle(store, Provider(), snapshot, now=NOW, scopes=(DecisionScope.PERP_INTRADAY,))
    assert result == {"perp_intraday": "success"}
    assert store.list_portfolio_soak_ticks()[0]["status"] == "success"
    assert store.load_parent_portfolio_state() is None
    assert store.venue_frame_count("hyperliquid") == 0
