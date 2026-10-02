from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.replay_v2.portfolio_book import PortfolioBook, PortfolioConfig


START = datetime(2024, 10, 2, tzinfo=timezone.utc)
MARKS = {symbol: Decimal(100) for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
ALL = {symbol: Decimal(1) for symbol in MARKS}


def config(**changes):
    return PortfolioConfig(**{"start": START, "end": START + timedelta(days=2), **changes})


def test_shared_cash_weights_costs_and_conservation():
    book = PortfolioBook(config())
    book.enter_batch(START, MARKS, ALL)
    assert [book.positions[s].quantity * 100 for s in sorted(MARKS)] == [300, 150, 150]
    assert book.cash == Decimal("399.1")
    assert book.equity(MARKS) == Decimal("999.1")
    for symbol in MARKS:
        book.close(symbol, START, Decimal(110), "test")
    assert book.cash == Decimal("1058.11")
    assert book.cash == 1000 + sum(t["net_pnl"] for t in book.trades)


def test_atr_is_applied_once_and_idle_budget_is_not_transferred():
    book = PortfolioBook(config())
    book.enter_batch(START, MARKS, {"BTCUSDT": Decimal(".5")})
    assert book.positions["BTCUSDT"].quantity * 100 == 150
    assert not book.positions["ETHUSDT"].quantity


def test_simultaneous_entries_are_order_independent_and_use_remaining_cap():
    results = []
    for symbols in (list(MARKS), list(reversed(MARKS))):
        book = PortfolioBook(config())
        book.enter_batch(START, MARKS, {"BTCUSDT": Decimal(1)})
        marks = {**MARKS, "BTCUSDT": Decimal(110)}
        book.enter_batch(START, marks, {s: ALL[s] for s in symbols if s != "BTCUSDT"})
        assert book.positions["ETHUSDT"].quantity * 100 == 135
        assert book.positions["SOLUSDT"].quantity * 100 == 135
        results.append((book.cash, book.events))
    assert results[0] == results[1]


def test_daily_loss_flattens_all_and_resumes_only_next_utc_day():
    book = PortfolioBook(config(daily_loss=".03", max_drawdown=".10"))
    book.enter_batch(START, MARKS, ALL)
    falling = {s: Decimal(94) for s in MARKS}
    book.enforce_risk(START + timedelta(hours=4), falling)
    assert book.halt_reason == "daily_loss_limit"
    assert all(not p.quantity for p in book.positions.values())
    book.enter_batch(START + timedelta(hours=8), MARKS, ALL)
    assert sum(e["kind"] == "entry" for e in book.events) == 3
    book.advance_day(START + timedelta(hours=20))
    assert book.halted
    peak = book.peak
    book.advance_day(START + timedelta(days=1))
    assert not book.halted and book.peak == peak
    assert book.day_start == book.cash


def test_dd_has_priority_and_never_resets():
    book = PortfolioBook(config(daily_loss=".03", max_drawdown=".08"))
    book.enter_batch(START, MARKS, ALL)
    book.enforce_risk(START, {s: Decimal(80) for s in MARKS})
    assert book.halt_reason == "max_drawdown"
    book.advance_day(START + timedelta(days=1))
    assert book.halted
    assert not any(e["kind"] == "resume" for e in book.events)


def test_closing_fees_upgrade_daily_halt_to_terminal_dd():
    book = PortfolioBook(config(daily_loss=".03", max_drawdown=".08"))
    book.enter_batch(START, MARKS, ALL)
    # Equity before closing = 920.14; closing costs put it below 920.
    falling = {s: Decimal("86.84") for s in MARKS}
    book.enforce_risk(START, falling)
    assert book.halt_reason == "max_drawdown"
    book.advance_day(START + timedelta(days=1))
    assert book.halted


@pytest.mark.parametrize("changes", [
    {"daily_loss": "NaN"}, {"capital": "Infinity"}, {"daily_loss": 0},
    {"max_drawdown": 1}, {"start": START.replace(tzinfo=None)},
    {"start": START + timedelta(minutes=1)},
])
def test_invalid_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        config(**changes)
