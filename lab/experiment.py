"""Offline Vibe-Trading execution with an independent cash/share audit."""

import contextlib
import json
import math
import shutil
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from backtest.engines.global_equity import GlobalEquityEngine

from lab.data import ROOT, digest, load
from lab.contracts import ExperimentSpec, RunSummary
from lab.strategy import SignalEngine


def read_config(path=None):
    path = Path(path) if path else ROOT / "experiment.json"
    return ExperimentSpec.model_validate_json(path.read_text())


class SnapshotLoader:
    name = "local-validated-yahoo-snapshot"

    def __init__(self, frame):
        self.frame = frame

    def fetch(self, codes, start_date, end_date, **kwargs):
        return {code: self.frame.loc[start_date:end_date].copy() for code in codes}


def audit(engine, frame, signals, config):
    """Replay fills as a cash account; verify quotes, causality and each daily equity."""
    cash = config["initial_cash"]
    quantity = 0.0
    fills_by_date = {}
    for fill in engine.fill_records:
        fills_by_date.setdefault(fill.timestamp, []).append(fill)
    held_days = 0
    max_error = 0.0
    for snapshot in engine.equity_snapshots:
        date = snapshot.timestamp
        fills = fills_by_date.get(date, [])
        terminal = []
        for fill in fills:
            if fill.reason == "end_of_backtest":
                terminal.append(fill)
                continue
            prior = frame.index[frame.index.get_loc(date) - 1]
            expected_target = signals.loc[prior]
            if (fill.signed_quantity > 0) != bool(expected_target):
                raise AssertionError(f"Order contradicts previous close signal: {date}")
            direction = 1 if fill.signed_quantity > 0 else -1
            expected = frame.loc[date, "open"] * (1 + direction * config["slippage_us"])
            if not math.isclose(fill.execution_price, expected, rel_tol=1e-10):
                raise AssertionError(f"Incorrect next-open fill: {date}")
            cash -= fill.signed_quantity * fill.execution_price + fill.fee
            quantity += fill.signed_quantity
        prior = frame.index[frame.index.get_loc(date) - 1]
        if (quantity > 1e-8) != bool(signals.loc[prior]):
            raise AssertionError(f"Position does not match previous signal: {date}")
        held_days += quantity > 1e-8
        for fill in terminal:
            if date != engine.equity_snapshots[-1].timestamp:
                raise AssertionError("Premature terminal liquidation")
            expected = frame.loc[date, "close"] * (1 - config["slippage_us"])
            if not math.isclose(fill.execution_price, expected, rel_tol=1e-10):
                raise AssertionError("Incorrect terminal close fill")
            cash -= fill.signed_quantity * fill.execution_price + fill.fee
            quantity += fill.signed_quantity
        if cash < -0.01 or quantity < -1e-8:
            raise AssertionError("Negative cash or short position in unlevered experiment")
        equity = cash + quantity * frame.loc[date, "close"]
        max_error = max(max_error, abs(equity - snapshot.equity))
        if not math.isclose(equity, snapshot.equity, rel_tol=1e-9, abs_tol=1e-6):
            raise AssertionError(f"Cash/share ledger differs at {date}")
    if abs(quantity) > 1e-8:
        raise AssertionError("Unclosed terminal position")
    if not math.isclose(cash, config["initial_cash"] + sum(t.pnl for t in engine.trades), abs_tol=1e-6):
        raise AssertionError("Trade P&L does not reconcile to final cash")
    return {"fills_checked": len(engine.fill_records), "sessions_checked": len(engine.equity_snapshots),
            "max_equity_error_usd": max_error, "held_days": int(held_days)}


def run_case(frame, config, output, buy_and_hold=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "artifacts").mkdir()
    (output / "code").mkdir()
    shutil.copyfile(ROOT / "lab/strategy.py", output / "code/signal_engine.py")
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    strategy = SignalEngine(config["fast_window"], config["slow_window"], buy_and_hold)
    inputs = frame.loc[config["start_date"]:config["end_date"]]
    first = inputs.index.searchsorted(pd.Timestamp(config["evaluation_start_date"]))
    if first < config["slow_window"] or first >= len(inputs) - 1:
        raise ValueError("Need full indicator warmup and at least two evaluation sessions")
    engine = GlobalEquityEngine(config, market="us")
    with (output / "engine-metrics.json").open("w") as log, contextlib.redirect_stdout(log):
        engine.run_backtest(config, SnapshotLoader(inputs), strategy, output)
    symbol = config["codes"][0]
    signals = strategy.generate({symbol: inputs})[symbol]
    evidence = audit(engine, inputs, signals, config)
    (output / "audit.json").write_text(json.dumps(evidence, indent=2) + "\n")
    records = [asdict(fill) for fill in engine.fill_records]
    pd.DataFrame(records, columns=["symbol", "timestamp", "bar_idx", "action", "signed_quantity",
                                 "notional", "execution_price", "fee", "margin", "reason", "holding_bars"]
                 ).to_csv(output / "fills-exact.csv", index=False, float_format="%.12g")
    curve = pd.Series([s.equity for s in engine.equity_snapshots],
                      index=[s.timestamp for s in engine.equity_snapshots], name="equity")
    curve.index.name = "date"
    curve.to_csv(output / "equity.csv", float_format="%.12g")
    # Include starting cash in the running peak: first-session costs are a drawdown too.
    values = np.r_[config["initial_cash"], curve.values]
    drawdown = values / np.maximum.accumulate(values) - 1
    summary = {
        "final_equity": float(curve.iloc[-1]),
        "total_return": float(curve.iloc[-1] / config["initial_cash"] - 1),
        "cagr_252": float((curve.iloc[-1] / config["initial_cash"]) ** (252 / len(curve)) - 1),
        "max_drawdown": float(drawdown.min()),
        "round_trips": len(engine.trades), "fills": len(engine.fill_records),
        "time_in_market": evidence["held_days"] / len(curve),
        "mean_holding_sessions": float(np.mean([t.holding_bars for t in engine.trades])) if engine.trades else 0,
        "sessions": len(curve), "start": str(curve.index[0].date()), "end": str(curve.index[-1].date()),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def run(config: ExperimentSpec, output):
    frame, manifest = load(config)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "source").mkdir()
    source_files = [ROOT / "pyproject.toml", ROOT / "uv.lock", *sorted((ROOT / "lab").glob("*.py"))]
    for path in source_files:
        shutil.copyfile(path, output / "source" / path.name)
    canonical_config = output / "source" / "experiment.json"
    canonical_config.write_text(json.dumps(config.to_json_dict(), indent=2) + "\n")
    provenance = {
        "engine": f"vibe-trading-ai=={version('vibe-trading-ai')}", "data": manifest,
        "source_hashes": {
            "experiment.json": digest(canonical_config),
            **{str(p.relative_to(ROOT)): digest(p) for p in source_files},
        },
        "experiment": config.to_json_dict(),
        "benchmark": "Separate buy-and-hold run using identical engine, snapshot and costs",
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    summaries = {}
    for period, (start, end) in config.periods.items():
        for bps in config.slippage_bps:
            for strategy in ("sma", "buy-hold"):
                cost_label = f"{bps:g}bps"
                key = f"{period}/{cost_label}/{strategy}"
                case = {
                    "codes": [config.engine_symbol], "source": "local", "interval": "1D",
                    "start_date": str(config.data.start), "end_date": str(end),
                    "evaluation_start_date": str(start), "initial_cash": config.initial_cash,
                    "leverage": 1.0, "position_adjustment": "hold", "slippage_us": bps / 10000,
                    "fast_window": config.strategy.fast_window, "slow_window": config.strategy.slow_window,
                    "buy_and_hold": strategy == "buy-hold", "commission": 0.0,
                }
                summaries[key] = RunSummary.model_validate(
                    run_case(frame, case, output / key, case["buy_and_hold"])
                ).model_dump(mode="json")
    (output / "summary.json").write_text(json.dumps(summaries, indent=2, allow_nan=False) + "\n")
    return summaries


def compare(first, second):
    """Compare numeric artifacts, ignoring engine timestamps and chart metadata."""
    first, second = Path(first), Path(second)
    for name in ("provenance.json", "summary.json"):
        if (first / name).read_bytes() != (second / name).read_bytes():
            raise AssertionError(f"Replay differs: {name}")
    cases = json.loads((first / "summary.json").read_text())
    for case in cases:
        for name in ("fills-exact.csv", "equity.csv", "audit.json"):
            if (first / case / name).read_bytes() != (second / case / name).read_bytes():
                raise AssertionError(f"Replay differs: {case}/{name}")
    return {"identical": True, "cases": len(cases), "files_compared": 2 + 3 * len(cases)}


def compare_results(first, second, tolerance=1e-8):
    """Compare research results across refactors while allowing provenance changes."""
    first, second = Path(first), Path(second)
    left = json.loads((first / "summary.json").read_text())
    right = json.loads((second / "summary.json").read_text())
    if left.keys() != right.keys():
        raise AssertionError("Result cases differ")
    for case in left:
        if left[case].keys() != right[case].keys():
            raise AssertionError(f"Summary fields differ: {case}")
        for field, expected in left[case].items():
            actual = right[case][field]
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                if not np.isclose(expected, actual, rtol=tolerance, atol=tolerance):
                    raise AssertionError(f"Metric differs: {case}/{field}")
            elif expected != actual:
                raise AssertionError(f"Metric differs: {case}/{field}")
    for case in left:
        for name in ("fills-exact.csv", "equity.csv"):
            expected = pd.read_csv(first / case / name)
            actual = pd.read_csv(second / case / name)
            try:
                pd.testing.assert_frame_equal(expected, actual, rtol=tolerance, atol=tolerance)
            except AssertionError as exc:
                raise AssertionError(f"Artifact differs: {case}/{name}") from exc
    return {"equivalent": True, "cases": len(left), "tolerance": tolerance}
