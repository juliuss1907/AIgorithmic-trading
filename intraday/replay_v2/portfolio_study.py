"""Private snapshot preparation and immutable reports for portfolio research."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from intraday.backups import create_backup, verify_backup
from intraday.replay_v2.artifacts import _write, publish_report, read_report
from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.portfolio_book import PortfolioConfig
from intraday.replay_v2.portfolio_research import DAY_MS, daily_points, simulate_portfolio, validate_inputs
from intraday.spot_signal import BinanceSpotDailyClient
from intraday.store import IntradayStore


START = datetime(2024, 10, 2, tzinfo=timezone.utc)
END = datetime(2026, 10, 2, tzinfo=timezone.utc)
FIVE_WEIGHTS = {"BTCUSDT": ".40", "ETHUSDT": ".20", "SOLUSDT": ".20",
                "NEARUSDT": ".10", "ZECUSDT": ".10"}


def study_configs(start, end, preset):
    if preset == "five-coin-confirmation":
        return [("30/8+EMA50-1D", PortfolioConfig(start=start, end=end, trend_filter=True,
                    daily_loss=daily, max_drawdown=".10", weights=FIVE_WEIGHTS, entry_cap=".65"))
                for daily in (".03", ".05")]
    if preset != "three-coin-grid":
        raise ValueError("unknown portfolio study preset")
    setups = (("30/8", 8, False), ("30/6", 6, False), ("30/10", 10, False), ("30/8+EMA50-1D", 8, True))
    return [(name, PortfolioConfig(start=start, end=end, exit_window=exit_window, trend_filter=trend,
                                  daily_loss=daily, max_drawdown=dd))
            for name, exit_window, trend in setups for dd in (".08", ".10") for daily in (".03", ".05")]


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_inputs(store, config):
    candles, daily = {}, {}
    for symbol in sorted(config.weights):
        candles[symbol] = tuple(Candle.from_row(row) for row in store.list_asset_candles(
            symbol, "4h", as_of=config.end) if row[0] >= int((config.start-timedelta(hours=124)).timestamp()*1000))
        daily[symbol] = tuple(row for row in store.list_asset_candles(symbol, "1d", as_of=config.end)
            if int((config.start-timedelta(days=60)).timestamp()*1000) <= row[0] < int(config.end.timestamp()*1000))
    return candles, daily


def collect_missing_daily(store, config, client):
    collected = []
    start = int((config.start.replace(hour=0)-timedelta(days=60)).timestamp()*1000)
    end = int(config.end.replace(hour=0).timestamp()*1000)
    for symbol in sorted(config.weights):
        existing = {int(row[0]) for row in store.list_asset_candles(symbol, "1d")}
        missing = [t for t in range(start, end, DAY_MS) if t not in existing]
        ranges = []
        for at in missing:
            if ranges and ranges[-1][1] == at:
                ranges[-1] = (ranges[-1][0], at+DAY_MS)
            else:
                ranges.append((at, at+DAY_MS))
        for first, exclusive_end in ranges:
            rows = client.backfill(symbol=symbol, interval="1d", start_time=first,
                                   end_time=exclusive_end-1, now=config.end)
            daily_points(rows)
            if [row[0] for row in rows] != list(range(first, exclusive_end, DAY_MS)):
                raise ValueError(f"{symbol}: public 1d backfill is incomplete")
            inserted = store.record_asset_candles(symbol, "1d", rows)
            collected.append({"symbol": symbol, "interval": "1d", "inserted_bars": inserted,
                "start_ms": first, "end_exclusive_ms": exclusive_end,
                "source": "https://api.binance.com/api/v3/klines",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "response_checksum": fingerprint(rows), "existing_bars_not_refetched": True})
    return collected


def comparison_markdown(receipt):
    title = " / ".join(s.removesuffix("USDT") for s in receipt["weights"])
    weights = ", ".join(f"{s.removesuffix('USDT')}={float(w)*100:g}%" for s, w in receipt["weights"].items())
    lines = [f"# {title} portfolio research", "",
        "Research only: no activation, official gate, Jev/LLM replay, or independent holdout.", "",
        f"Shared {receipt['capital']:g} USDT; weights {weights}; entry cap {receipt['entry_cap_pct']:g}%, ATR14; native 4h signals.",
        "10bps fee + 5bps assumed slippage each fill; 10% per-coin emergency stop.",
        "Portfolio risk sampled at 4h open/close and after costs; intrabar DD unknown.", "",
        "| Setup | Daily loss | DD limit | Net return | Observed DD | Trades | Research | Terminal halt UTC+7 |",
        "|---|---:|---:|---:|---:|---:|---|---|"]
    for result in receipt["results"]:
        summary = result["summary"]
        halted = summary["terminal_halt_at"]
        local = datetime.fromisoformat(halted).astimezone(timezone(timedelta(hours=7))).isoformat() if halted else "none"
        lines.append(f"| {result['variant']} | {result['daily_loss_pct']:g}% | {result['max_drawdown_pct']:g}% | "
            f"{summary['net_return_pct']:.3f}% | {summary['max_drawdown_known_pct']:.3f}% | "
            f"{summary['closed_trades']} | {summary['economic_check_only']} | {local} |")
    lines.extend(["", "Full curve, fills, events, per-coin PnL and six-month periods are in the report bundles.",
        "Halts can leave the account idle for the remaining window; compare active days as well as returns.",
        "These results are not directly comparable to earlier independent accounts with different caps/risk sampling.", ""])
    return "\n".join(lines)


def run_study(database, report_root, *, start=START, end=END, collect=False, client_factory=BinanceSpotDailyClient,
              progress=None, preset="three-coin-grid"):
    variants = study_configs(start, end, preset)
    config = variants[0][1]
    source = Path(database).resolve()
    verified = verify_backup(source)
    before = file_hash(source)
    source_store = IntradayStore(source, read_only=True)
    candles, daily = load_inputs(source_store, config)
    validate_inputs(config.model_copy(update={"trend_filter": False}), candles, daily)
    root = Path(report_root).resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    working = root/"research.sqlite3"
    fd = os.open(working, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(working)) as target:
            original.backup(target)
    collected = []
    if collect:
        writer = IntradayStore(working)
        client = client_factory(asset_catalog=writer.asset_catalog(), timeout_seconds=15)
        collected = collect_missing_daily(writer, config, client)
    evidence = create_backup(working, root/"evidence")
    verify_backup(evidence["backup"])
    evidence_source = Path(evidence["backup"])
    frozen = file_hash(evidence_source)
    candles, daily = load_inputs(IntradayStore(evidence_source, read_only=True), config)
    validate_inputs(config.model_copy(update={"trend_filter": True}), candles, daily)
    if progress:
        progress({"phase": "evidence_ready", "collected": collected, "evidence": str(evidence_source)})
    results = []
    for name, variant in variants:
        report = simulate_portfolio(variant, candles, daily)
        report["inputs"]["evidence_sha256"] = frozen
        saved = publish_report(root/"reports", report)
        loaded = read_report(root/"reports", saved["run_id"])
        if loaded["result_id"] != report["result_id"]:
            raise ValueError("published portfolio report identity mismatch")
        item = {"variant": name, "daily_loss_pct": float(variant.daily_loss*100),
            "max_drawdown_pct": float(variant.max_drawdown*100),
            "run_id": saved["run_id"], "result_id": report["result_id"],
            "dataset_checksum": report["inputs"]["dataset_checksum"], "summary": report["summary"]}
        results.append(item)
        if progress:
            progress({"phase": "replay_complete", "variant": name,
                "daily_loss_pct": item["daily_loss_pct"], "dd_limit_pct": item["max_drawdown_pct"],
                **{k: item["summary"][k] for k in ("net_return_pct", "max_drawdown_known_pct", "closed_trades", "economic_check_only")}})
    after, after_evidence = file_hash(source), file_hash(evidence_source)
    if before != after or frozen != after_evidence:
        raise ValueError("immutable evidence changed during portfolio research")
    if len({r["result_id"] for r in results}) != len(variants) or len({r["dataset_checksum"] for r in results}) != 1:
        raise ValueError("portfolio grid identities or shared dataset mismatch")
    receipt = {"research_only": True, "activation_allowed": False, "official_gate_eligible": False,
        "window": {"start": config.start.isoformat(), "end": config.end.isoformat()},
        "original_source": str(source), "source_manifest": verified, "source_hash_before": before,
        "source_hash_after": after, "source_unchanged": True, "evidence": evidence,
        "evidence_unchanged": True, "collection": collected,
        "bars_per_coin": int((config.end-config.start)/timedelta(hours=4)), "coverage_per_coin": 1,
        "capital": float(config.capital), "weights": {s: str(w) for s, w in config.weights.items()},
        "entry_cap_pct": float(config.entry_cap*100), "preset": preset,
        "results": results}
    _write(root/"comparison.json", json.dumps(receipt, indent=2, allow_nan=False))
    _write(root/"comparison.md", comparison_markdown(receipt))
    return receipt
