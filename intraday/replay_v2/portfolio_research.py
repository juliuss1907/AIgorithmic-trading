"""Offline weighted Spot portfolio study; no lifecycle writes or model calls."""

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from intraday.contracts import SpotRuleParameters
from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.indicators import ema50_trend, trend_side
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.portfolio_book import STOP, PortfolioBook, PortfolioConfig
from intraday.replay_v2.study import months_before
from intraday.spot_signal import evaluate_donchian


VERSION = "portfolio-research-4h-v1"
WIDTH = timedelta(hours=4)
DAY_MS = 86400000


def daily_points(rows):
    """Validate native daily OHLC, then seed EMA50 with its first 50 closes."""
    return [(available, side == 1) for available, side in daily_trend(rows)]


def daily_trend(rows):
    """Validate native daily OHLC once; return (available_at, EMA50 side) per day."""
    times, closes, previous = [], [], None
    for row in rows:
        if len(row) < 7:
            raise ValueError("invalid native 1d row")
        opening = int(row[0])
        if opening != row[0] or opening % DAY_MS or int(row[6]) != opening + DAY_MS - 1:
            raise ValueError("invalid native 1d timestamps")
        if previous is not None and opening != previous + DAY_MS:
            raise ValueError("native 1d history gap")
        prices = [Decimal(str(v)) for v in row[1:5]]
        volume = Decimal(str(row[5]))
        if any(not v.is_finite() or v <= 0 for v in prices) or not volume.is_finite() or volume < 0:
            raise ValueError("invalid native 1d values")
        op, high, low, close = prices
        if not low <= min(op, close) <= max(op, close) <= high:
            raise ValueError("invalid native 1d OHLC")
        closes.append(close)
        times.append(datetime.fromtimestamp((opening + DAY_MS)/1000, timezone.utc))
        previous = opening
    return [(available, trend_side(close, ema, prior))
            for available, close, (ema, prior) in zip(times, closes, ema50_trend(closes))]


def validate_inputs(config, candles, daily):
    if set(candles) != set(config.weights):
        raise ValueError("4h inputs require every configured portfolio asset")
    timeline = tuple(config.start + i * WIDTH for i in range(int((config.end-config.start)/WIDTH)))
    prepared, trends = {}, {}
    for symbol in sorted(config.weights):
        rows = tuple(c for c in candles[symbol] if c.available_at <= config.end)
        if any(a.available_at != b.opened_at for a, b in zip(rows, rows[1:])):
            raise ValueError(f"{symbol}: 4h history gap or duplicate")
        used = tuple(c.opened_at for c in rows if config.start <= c.opened_at < config.end)
        if used != timeline or sum(c.available_at <= config.start for c in rows) < 31:
            raise ValueError(f"{symbol}: incomplete 4h window or warmup")
        prepared[symbol] = rows
        if config.trend_filter:
            points = daily_points(daily.get(symbol, ()))
            available = [p[0] for p in points]
            count = bisect_right(available, config.start)
            if count < 51 or available[count-1] != config.start.replace(hour=0):
                raise ValueError(f"{symbol}: incomplete 1d warmup")
            last_needed = (config.end-WIDTH).replace(hour=0)
            if not available or available[-1] < last_needed:
                raise ValueError(f"{symbol}: incomplete 1d window")
            trends[symbol] = (available, points)
    return timeline, prepared, trends


def six_month_summary(config, curve):
    boundaries = [config.start] + [months_before(config.end, n) for n in (18, 12, 6)] + [config.end]
    boundaries = sorted(set(t for t in boundaries if config.start <= t <= config.end))
    results, initial = [], config.capital
    for start, end in zip(boundaries, boundaries[1:]):
        values = [Decimal(p["equity_known"]) for p in curve if start <= datetime.fromisoformat(p["at"]) < end]
        peak, drawdown = initial, Decimal(0)
        for value in values:
            peak = max(peak, value)
            drawdown = max(drawdown, 1-value/peak)
        final = values[-1] if values else initial
        results.append({"start": start.isoformat(), "end": end.isoformat(),
                        "return_pct": float((final/initial-1)*100),
                        "pnl": float(final-initial), "max_drawdown_pct": float(drawdown*100)})
        initial = final
    return results


def portfolio_result(config, candles, daily, book):
    net = book.cash-config.capital  # All positions have been closed at window end.
    contributions = {}
    for symbol in sorted(config.weights):
        trades = [t for t in book.trades if t["symbol"] == symbol]
        contributions[symbol] = {"closed_trades": len(trades), **{
            name: float(sum((t[name] for t in trades), Decimal(0)))
            for name in ("gross_pnl", "exchange_fee", "slippage_cost", "net_pnl")}}
    if abs(sum((t["net_pnl"] for t in book.trades), Decimal(0))-net) > Decimal("1e-18"):
        raise ValueError("trade PnL does not reconcile to cash")
    for event in book.events:
        if event["kind"] == "entry":
            base = Decimal(event["batch_base_equity"])
            maximum = base*config.entry_cap*config.weights[event["symbol"]]
            if not 0 < Decimal(event["notional"]) <= Decimal(event["requested_notional"]) <= maximum:
                raise ValueError("per-coin allocation violation")
    blockers = []
    if net <= 0:
        blockers.append("nonpositive_net_return")
    if book.max_drawdown >= config.max_drawdown:
        blockers.append("drawdown_at_or_above_limit")
    if len(book.trades) < 6:
        blockers.append("minimum_6_closed_trades")
    halts = [e for e in book.events if e["kind"] == "halt"]
    entry_times = [e["at"] for e in book.events if e["kind"] == "entry"]
    terminal = next((e["at"] for e in halts if e["reason"] == "max_drawdown"), None)
    config_payload = {**config.model_dump(mode="json"),
        "symbol": "+".join(s.removesuffix("USDT") for s in sorted(config.weights)), "market": "spot",
        "weights": {s: str(w) for s, w in config.weights.items()}, "entry_cap": str(config.entry_cap),
        "entry_window": 30, "atr_period": 14, "fee_bps": 10, "slippage_bps": 5,
        "spot_stop_pct": 10, "risk_sampling": "native_4h_open_close_and_fill_costs"}
    input_hash = fingerprint({"candles": {s: [c.row() for c in candles[s]] for s in sorted(config.weights)},
                              "native_daily": daily})
    result_id = fingerprint({"version": VERSION, "config": config_payload, "data": input_hash})
    summary = {"initial_capital": float(config.capital), "final_equity_known": float(book.cash),
        "net_pnl": float(net), "pnl_after_known_costs": float(net),
        "net_return_pct": float(net/config.capital*100), "max_drawdown_known_pct": float(book.max_drawdown*100),
        "closed_trades": len(book.trades), "exchange_fee_known": float(book.fees),
        "slippage_cost_known": float(book.slippage), "max_exposure_pct": float(book.max_exposure*100),
        "halted": book.halted, "halt_reason": book.halt_reason, "terminal_halt_at": terminal,
        "daily_pause_count": sum(e.get("trigger_reason") == "daily_loss_limit" for e in halts),
        "daily_resume_count": sum(e["kind"] == "resume" for e in book.events),
        "active_days_until_terminal": float(((datetime.fromisoformat(terminal) if terminal else config.end)-config.start).total_seconds()/86400),
        "days_with_entries": len({t[:10] for t in entry_times}), "contributions": contributions,
        "six_month_periods": six_month_summary(config, book.curve),
        "hard_risk_violations": 0, "economic_check_only": "reject" if blockers else "pass",
        "blockers": blockers}
    return {"schema_version": "2", "evaluator_version": VERSION, "result_id": result_id,
        "research_only": True, "activation_allowed": False, "official_gate_eligible": False,
        "status": "complete", "config": config_payload,
        "inputs": {"dataset_checksum": input_hash, "config_checksum": fingerprint(config_payload)},
        "summary": summary, "methodology": {"risk_limits": {"daily_loss_pct": float(config.daily_loss*100),
            "max_drawdown_pct": float(config.max_drawdown*100), "spot_stop_pct": 10},
            "official_gate_eligible": False, "daily_reset": "next UTC day; equity peak preserved",
            "native_stop": "gap at open; low-touch at stop, recorded at 4h close as detection time",
            "allocation": "shared cash; proportional simultaneous entries; no transfer or rebalance"},
        "limitations": ["research_only_not_activation_gate", "ex_post_not_independent_holdout",
            "portfolio_drawdown_between_4h_samples_unknown", "risk_gaps_and_closing_costs_can_overshoot",
            "spot_jev_filter_not_replayed", "llm_not_called", "assumed_cash_charged_slippage",
            "stored_binance_prices_not_verified_historical_demo_quotes",
            "ideal_fractional_fills_without_order_book_partial_fills_or_instrument_filters"],
        "v1_reference": None, "equity_curve": book.curve, "trades": book.trades, "events": book.events}


def simulate_portfolio(config, candles, daily):
    timeline, prepared, trends = validate_inputs(config, candles, daily)
    rule = SpotRuleParameters(entry_window=30, exit_window=config.exit_window, atr_period=14)
    indexes = {s: {c.opened_at: i for i, c in enumerate(prepared[s])} for s in config.weights}
    raw = {s: [c.row() for c in prepared[s]] for s in config.weights}
    book = PortfolioBook(config)
    for at in timeline:
        bars = {s: prepared[s][indexes[s][at]] for s in config.weights}
        marks = {s: bars[s].open for s in config.weights}
        book.advance_day(at)
        book.enforce_risk(at, marks)
        just_closed, observations = set(), {}
        for symbol in sorted(config.weights):
            index = indexes[symbol][at]
            observations[symbol] = evaluate_donchian(raw[symbol][index-31:index], rule)
            position = book.positions[symbol]
            if position.quantity and marks[symbol] <= position.entry_price*(1-STOP):
                book.close(symbol, at, marks[symbol], "emergency_stop_gap")
                just_closed.add(symbol)
            elif position.quantity and observations[symbol].exit:
                book.close(symbol, at, marks[symbol], "donchian_exit")
                just_closed.add(symbol)
        book.enforce_risk(at, marks)
        requests = {}
        for symbol in sorted(config.weights):
            obs = observations[symbol]
            if not obs.entry or symbol in just_closed or book.positions[symbol].quantity:
                continue
            allowed = True
            if config.trend_filter:
                available, points = trends[symbol]
                allowed = points[bisect_right(available, at)-1][1]
            if allowed:
                requests[symbol] = Decimal(str(obs.size_multiplier))
            else:
                book.event(at, "entry_blocked", "daily_trend_filter", symbol=symbol)
        book.enter_batch(at, marks, requests)
        book.enforce_risk(at, marks)
        close_at = at + WIDTH - timedelta(milliseconds=1)
        close_marks = {s: bars[s].close for s in config.weights}
        for symbol in sorted(config.weights):
            position = book.positions[symbol]
            if position.quantity and bars[symbol].low <= position.entry_price*(1-STOP):
                book.close(symbol, close_at, position.entry_price*(1-STOP), "emergency_stop")
        book.enforce_risk(close_at, close_marks)
    for symbol in sorted(config.weights):
        book.close(symbol, close_at, close_marks[symbol], "window_end")
    book.enforce_risk(close_at, close_marks)
    return portfolio_result(config, prepared, daily, book)


def main(argv=None):
    import argparse
    import json
    from intraday.replay_v2.portfolio_study import run_study

    parser = argparse.ArgumentParser(description="Research-only weighted Spot portfolio study")
    parser.add_argument("--database", required=True, help="Verified immutable backup with sibling manifest")
    parser.add_argument("--report-root", required=True, help="New private study directory; never overwritten")
    parser.add_argument("--collect-missing-1d", action="store_true", help="Append missing public daily bars to a private copy")
    parser.add_argument("--preset", choices=("three-coin-grid", "five-coin-confirmation"), default="three-coin-grid",
                        help="Legacy 16-run grid or five-coin 65%% confirmation with the two passing settings")
    arguments = parser.parse_args(argv)
    receipt = run_study(arguments.database, arguments.report_root, collect=arguments.collect_missing_1d,
                        preset=arguments.preset,
                        progress=lambda item: print(json.dumps(item), flush=True))
    print(json.dumps({"comparison": str(arguments.report_root)+"/comparison.json", "runs": len(receipt["results"]),
                      "source_unchanged": receipt["source_unchanged"]}), flush=True)


if __name__ == "__main__":
    main()
