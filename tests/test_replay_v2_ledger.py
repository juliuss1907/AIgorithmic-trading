from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.replay_v2.contracts import ReplayConfig, ExecutionProfile, FundingHistory
from intraday.replay_v2.ledger import Ledger


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def ledger(market="spot", **kw):
    return Ledger(ReplayConfig(symbol="ETH", market=market, rule_id="r", start=NOW,
        end=NOW+timedelta(days=1), **kw))


def test_spot_cash_and_inventory_reconcile_including_both_fill_costs():
    book = ledger()
    assert book.enter(NOW, Decimal(100), Decimal(300), side=1, stop_distance=Decimal(".10"))
    assert book.quantity == 3
    assert book.cash == Decimal("699.55")
    assert book.equity(Decimal(110)) == Decimal("1029.55")
    book.close(NOW+timedelta(hours=1), Decimal(110), "donchian_exit")
    assert book.cash == Decimal("1029.055")
    assert book.realized == 30
    assert book.costs == Decimal(".945")
    assert Decimal(book.trades[0]["net_known_pnl"]) == Decimal("29.055")


@pytest.mark.parametrize("side", [1, -1])
def test_perp_notional_is_not_multiplied_by_leverage(side):
    books = [ledger("perp", leverage=leverage) for leverage in (3, 5)]
    for book in books:
        assert book.enter(NOW, Decimal(100), Decimal(200), side=side, stop_distance=Decimal(".01"))
        assert book.quantity == 2 * side
        book.close(NOW+timedelta(minutes=1), Decimal(101), "take_profit")
    assert books[0].cash == books[1].cash
    assert books[0].realized == Decimal(2 * side)


def test_margin_and_instrument_constraints_prevent_invalid_entries():
    book = ledger("perp", leverage=1)
    assert not book.enter(NOW, Decimal(100), Decimal(200), side=1, stop_distance=Decimal(".01"))
    assert book.quantity == 0
    assert book.events[-1]["reason"] == "isolated_margin_limit"
    instrument = {"symbol":"ETHUSDT", "quantity_step":"1", "min_quantity":"1",
                  "max_quantity":"100", "min_notional":"5", "price_tick":".01"}
    book = ledger(profile=ExecutionProfile(instrument=instrument,
        instrument_source="fixture", instrument_observed_at=NOW))
    assert book.enter(NOW, Decimal(110), Decimal(300), side=1, stop_distance=Decimal(".10"))
    assert book.quantity == 2


def test_daily_guard_is_terminal_and_day_boundary_uses_previous_equity():
    book = ledger()
    book.observe(NOW, Decimal(100))
    book.enter(NOW, Decimal(100), Decimal(300), side=1, stop_distance=Decimal(".10"))
    book.observe(NOW+timedelta(hours=1), Decimal(94))
    assert book.guard_reason(Decimal(94)) == "daily_loss_limit"
    book.halt(NOW+timedelta(hours=1), Decimal(94), "daily_loss_limit")
    assert not book.enter(NOW+timedelta(hours=2), Decimal(100), Decimal(300), side=1, stop_distance=Decimal(".10"))
    assert book.halted


def test_signed_funding_debits_long_and_credits_short():
    for side in (1, -1):
        book = ledger("perp")
        book.enter(NOW, Decimal(100), Decimal(200), side=side, stop_distance=Decimal(".01"))
        book.settle_funding(NOW+timedelta(hours=1), Decimal(".001"), Decimal(100))
        assert book.funding == Decimal(".2")*side
        assert book.cash == Decimal("999.9") - Decimal(".2")*side


def test_funding_coverage_requires_explicit_full_window():
    history = FundingHistory(symbol="ETH", source="fixture", coverage_start=NOW,
        coverage_end=NOW+timedelta(hours=1))
    assert not ledger("perp", profile=ExecutionProfile(funding=history)).funding_complete
    assert not ledger("perp").funding_complete
    assert ledger().funding_complete


def test_closing_cost_can_trip_terminal_guard_after_an_otherwise_safe_mark():
    book = ledger("perp")
    book.enter(NOW, Decimal(100), Decimal(200), side=1, stop_distance=Decimal(".01"))
    price = Decimal("92.595")
    assert book.guard_reason(price) is None
    book.close(NOW+timedelta(seconds=30), price, "protective_stop")
    assert book.halted
    assert book.halt_reason == "daily_loss_limit"
