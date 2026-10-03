from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.replay_v2.mixed_book import MixedBook, MixedConfig


START = datetime(2026, 9, 30, 4, tzinfo=timezone.utc)
SPOT = {s: Decimal(100) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "ZECUSDT")}


def config(**changes):
    return MixedConfig(start=START, end=START + timedelta(hours=4), **changes)


def marked_book(**changes):
    book = MixedBook(config(**changes))
    book.perp_marks = {"BTCUSDT": Decimal(100), "ETHUSDT": Decimal(100)}
    return book


def test_spot_and_perp_same_coin_have_separate_positions_and_shared_cash():
    book = marked_book()
    book.enter_batch(START, SPOT, {s: Decimal(1) for s in SPOT})
    assert book.positions["BTCUSDT"].quantity == Decimal("2.4")
    assert book.cash == Decimal("399.1")
    assert book.enter_perp("BTCUSDT", START, SPOT, Decimal(100), 1, Decimal(".02"))
    assert book.perps["BTCUSDT"].quantity == Decimal("1.49865")  # equity after Spot costs
    assert book.locked_margin == Decimal("49.955")
    assert book.cash > 398 and book.free_cash < 350
    assert book.positions["BTCUSDT"].quantity == Decimal("2.4")


@pytest.mark.parametrize("side,exit_price,rate", [(1, 110, ".001"), (-1, 90, ".001"), (-1, 110, "-.001")])
def test_perp_pnl_funding_and_margin_release_reconcile(side, exit_price, rate):
    book = marked_book()
    book.enter_perp("BTCUSDT", START, SPOT, Decimal(100), side, Decimal(".02"))
    book.settle_funding("BTCUSDT", START, Decimal(rate), Decimal(100))
    assert book.locked_margin == 50
    book.close_perp("BTCUSDT", START, Decimal(exit_price), "test")
    assert book.locked_margin == 0
    assert book.cash == 1000 + sum(t["net_pnl"] for t in book.trades)
    assert book.trades[0]["market"] == "perp"
    assert book.trades[0]["funding_paid"] == Decimal(side) * Decimal("1.5") * 100 * Decimal(rate)


def test_margin_and_reserve_cannot_be_spent_again():
    book = marked_book()
    book.enter_perp("BTCUSDT", START, SPOT, Decimal(100), 1, Decimal(".02"))
    book.cash = Decimal(151)
    book.enter_batch(START, SPOT, {"BTCUSDT": Decimal(1)})
    assert book.free_cash >= min(book.config.capital, book.equity(SPOT)) * Decimal(".10")
    assert book.positions["BTCUSDT"].quantity * 100 < 240


def test_daily_halt_waits_for_actual_flatten_then_resumes_without_resetting_peak():
    book = marked_book()
    book.enter_batch(START, SPOT, {s: Decimal(1) for s in SPOT})
    falling = {s: Decimal(94) for s in SPOT}
    book.enforce_risk(START, falling)
    assert book.halt_reason == "daily_loss_limit"
    assert book.positions["BTCUSDT"].quantity  # risk doesn't fabricate a Spot fill at a tick
    book.advance_day(START + timedelta(days=1))
    assert book.halted
    for s in SPOT:
        book.close(s, START + timedelta(days=1), falling[s], "daily_loss_limit")
    peak = book.peak
    book.maybe_resume(START + timedelta(days=1), falling)
    assert not book.halted and book.peak == peak


def test_terminal_dd_has_priority_and_is_never_resumed():
    book = marked_book()
    book.enter_batch(START, SPOT, {s: Decimal(1) for s in SPOT})
    falling = {s: Decimal(80) for s in SPOT}
    book.enforce_risk(START, falling)
    assert book.halt_reason == "max_drawdown"
    for s in SPOT:
        book.close(s, START, falling[s], "max_drawdown")
    book.advance_day(START + timedelta(days=1))
    book.maybe_resume(START + timedelta(days=1), falling)
    assert book.halted


@pytest.mark.parametrize("changes", [{"perp_cap": "NaN"}, {"perp_cap": ".5"},
    {"reserve": 0}, {"leverage": 0}, {"perp_weights": {"BTCUSDT": ".7"}}])
def test_invalid_mixed_allocations_fail(changes):
    with pytest.raises(ValueError):
        config(**changes)
