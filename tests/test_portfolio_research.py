from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.replay_v2.portfolio_book import PortfolioBook, PortfolioConfig
from intraday.replay_v2.contracts import Candle


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


def candles(*, breakout=True, volatile=False, future=None):
    rows = []
    for index in range(-31, 0):
        close = 110 if breakout and index == -1 else 100
        rows.append(Candle(opened_at=START + timedelta(hours=4*index),
            available_at=START + timedelta(hours=4*(index+1)), open=100,
            high=max(close, 120 if volatile else Decimal("100.1")),
            low=80 if volatile else Decimal("99.9"), close=close, volume=1))
    future = future or [(110, 111, 109, 110)]
    for index, (opening, high, low, close) in enumerate(future):
        rows.append(Candle(opened_at=START + timedelta(hours=4*index),
            available_at=START + timedelta(hours=4*(index+1)),
            open=opening, high=high, low=low, close=close, volume=1))
    return {s: tuple(rows) for s in MARKS}


def daily_rows(*, rising=True):
    rows = []
    for i in range(-60, 2):
        opening = int((START + timedelta(days=i)).timestamp()*1000)
        price = str(100 + i if rising else 100)
        rows.append([opening, price, price, price, price, "1", opening+86400000-1])
    return {s: tuple(rows) for s in MARKS}


def test_replay_three_assets_next_open_costs_and_determinism():
    from intraday.replay_v2.portfolio_research import simulate_portfolio

    cfg = config(end=START + timedelta(hours=4))
    report = simulate_portfolio(cfg, candles(), {})
    assert report == simulate_portfolio(cfg, dict(reversed(list(candles().items()))), {})
    assert report["summary"]["net_pnl"] == pytest.approx(-1.8)
    entries = [e for e in report["events"] if e["kind"] == "entry"]
    assert len(entries) == 3 and all(e["at"] == START.isoformat() for e in entries)
    assert all(e["price"] == "110" for e in entries)
    assert sum(t["net_pnl"] for t in report["trades"]) == Decimal("-1.8")
    assert report["activation_allowed"] is False
    assert report["official_gate_eligible"] is False
    # Reproduction uses the original serialized policy, not rounded percentages.
    restored = PortfolioConfig.model_validate({k: report["config"][k] for k in PortfolioConfig.model_fields})
    assert simulate_portfolio(restored, candles(), {}) == report


def test_current_candle_does_not_generate_its_own_entry():
    from intraday.replay_v2.portfolio_research import simulate_portfolio

    report = simulate_portfolio(config(end=START + timedelta(hours=4)),
                                candles(breakout=False), {})
    assert report["summary"]["closed_trades"] == 0


def test_daily_filter_uses_only_available_closed_candles():
    from intraday.replay_v2.portfolio_research import simulate_portfolio

    cfg = config(end=START + timedelta(hours=4), trend_filter=True)
    flat = daily_rows(rising=False)
    # Future day has an enormous rally, but is not available at the entry.
    for rows in flat.values():
        rows[-1][1:5] = ["10000"]*4
    assert simulate_portfolio(cfg, candles(), flat)["summary"]["closed_trades"] == 0
    assert simulate_portfolio(cfg, candles(), daily_rows())["summary"]["closed_trades"] == 3


def test_missing_4h_or_daily_data_fails_closed():
    from intraday.replay_v2.portfolio_research import simulate_portfolio

    data = candles()
    data["SOLUSDT"] = data["SOLUSDT"][:-1]
    with pytest.raises(ValueError, match="4h"):
        simulate_portfolio(config(end=START + timedelta(hours=4)), data, {})
    with pytest.raises(ValueError, match="1d"):
        simulate_portfolio(config(end=START + timedelta(hours=4), trend_filter=True), candles(), {})


def test_atr_sizes_each_asset_once_in_replay():
    from intraday.replay_v2.portfolio_research import simulate_portfolio

    data = candles(volatile=True)
    # Volatile highs (120) would suppress a breakout at 110. Use a higher setup.
    data = {s: tuple(list(rows[:-2]) + [rows[-2].model_copy(update={
        "close": Decimal(130), "high": Decimal(131)}), rows[-1].model_copy(update={
        "open": Decimal(130), "high": Decimal(131), "low": Decimal(129), "close": Decimal(130)})])
        for s, rows in data.items()}
    result = simulate_portfolio(config(end=START + timedelta(hours=4)), data, {})
    entry = next(e for e in result["events"] if e["kind"] == "entry" and e["symbol"] == "BTCUSDT")
    assert 18 < Decimal(entry["notional"]) < 20


def test_coin_stop_uses_low_but_portfolio_does_not_use_simultaneous_lows():
    from intraday.replay_v2.portfolio_research import simulate_portfolio

    result = simulate_portfolio(config(end=START + timedelta(hours=4)),
        candles(future=[(110, 111, 90, 110)]), {})
    assert all(t["exit_reason"] == "emergency_stop" for t in result["trades"])
    assert all(Decimal(t["exit_price"]) == 99 for t in result["trades"])
    assert result["summary"]["halt_reason"] == "daily_loss_limit"
    assert result["summary"]["max_drawdown_known_pct"] < 10


def test_coin_gap_stop_fills_at_open_when_portfolio_guard_is_not_triggered():
    from intraday.replay_v2.portfolio_research import simulate_portfolio

    data = candles(volatile=True, future=[(130, 131, 129, 130), (110, 111, 109, 110)])
    data = {s: tuple(list(rows[:-3]) + [rows[-3].model_copy(update={
        "close": Decimal(130), "high": Decimal(131)})] + list(rows[-2:])) for s, rows in data.items()}
    result = simulate_portfolio(config(end=START + timedelta(hours=8)), data, {})
    assert all(t["exit_reason"] == "emergency_stop_gap" for t in result["trades"])
    assert all(Decimal(t["exit_price"]) == 110 for t in result["trades"])
    assert not result["summary"]["halted"]


def test_native_daily_gaps_and_corrupt_prices_are_rejected():
    from intraday.replay_v2.portfolio_research import daily_points

    rows = list(daily_rows()["BTCUSDT"])
    with pytest.raises(ValueError, match="1d history gap"):
        daily_points(rows[:20] + rows[21:])
    rows[0][4] = "NaN"
    with pytest.raises(ValueError, match="1d values"):
        daily_points(rows)


def test_batch_reports_16_variants_and_keeps_verified_source_unchanged(tmp_path):
    from intraday.backups import create_backup, verify_backup
    from intraday.replay_v2.artifacts import read_report
    from intraday.replay_v2.portfolio_study import run_study
    from intraday.store import IntradayStore

    store = IntradayStore(tmp_path / "input.sqlite3")
    data = candles()
    for symbol in MARKS:
        store.register_asset(symbol, market="spot", now=START)
        store.record_asset_candles(symbol, "4h", [c.row() for c in data[symbol]])
        store.record_asset_candles(symbol, "1d", list(daily_rows()[symbol]))
    source = create_backup(store.database, tmp_path / "source")
    receipt = run_study(source["backup"], tmp_path / "study", start=START,
                        end=START + timedelta(hours=4))
    assert len(receipt["results"]) == 16
    assert len({r["result_id"] for r in receipt["results"]}) == 16
    assert len({r["dataset_checksum"] for r in receipt["results"]}) == 1
    assert receipt["source_unchanged"] and receipt["evidence_unchanged"]
    assert verify_backup(source["backup"])["sha256"] == source["sha256"]
    assert receipt["coverage_per_coin"] == 1
    for result in receipt["results"]:
        report = read_report(tmp_path / "study" / "reports", result["run_id"])
        assert report["evaluator_version"] == "portfolio-research-4h-v1"
        assert report["methodology"]["official_gate_eligible"] is False


def test_missing_daily_collection_requests_only_missing_ranges(tmp_path):
    from intraday.replay_v2.portfolio_study import collect_missing_daily
    from intraday.store import IntradayStore

    store = IntradayStore(tmp_path / "copy.sqlite3")
    all_rows = daily_rows()
    for symbol in MARKS:
        store.register_asset(symbol, market="spot", now=START)
        store.record_asset_candles(symbol, "1d", list(all_rows[symbol][:10] + all_rows[symbol][12:]))
    calls = []

    class PublicClient:
        def backfill(self, *, symbol, interval, start_time, end_time, now):
            calls.append((symbol, interval, start_time, end_time))
            return [r for r in all_rows[symbol] if start_time <= r[0] <= end_time]

    collected = collect_missing_daily(store, config(), PublicClient())
    assert len(calls) == 3 and all(c[1] == "1d" for c in calls)
    assert all(c[2] == all_rows[c[0]][10][0] and c[3] == all_rows[c[0]][12][0]-1 for c in calls)
    assert all(r["inserted_bars"] == 2 for r in collected)
