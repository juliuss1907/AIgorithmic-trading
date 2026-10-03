"""Explicit offline Spot+Perp study; no scheduler, lifecycle or credentials."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from intraday.backups import create_backup, verify_backup
from intraday.replay_v2.artifacts import SERIES, _write, publish_report, read_report
from intraday.replay_v2.contracts import ReplayConfig, binance_gate_profile
from intraday.replay_v2.data import load_dataset
from intraday.replay_v2.funding import fetch_funding_snapshot, read_funding_snapshot, save_funding_snapshot
from intraday.replay_v2.mixed_book import MixedConfig
from intraday.replay_v2.mixed_research import perp_diagnostics, simulate_mixed
from intraday.replay_v2.metrics import encoded
from intraday.replay_v2.portfolio_research import validate_inputs
from intraday.replay_v2.portfolio_study import file_hash, load_inputs
from intraday.store import IntradayStore


START = datetime(2026, 9, 30, 4, tzinfo=timezone.utc)
END = datetime(2026, 10, 2, tzinfo=timezone.utc)
RULES = {"BTCUSDT": "perp-rule-v1", "ETHUSDT": "ethusdt-perp-baseline-v1"}


def load_mixed_inputs(store, config):
    candles, daily = load_inputs(store, config)
    data = {s: load_dataset(store.database, ReplayConfig(symbol=s, market="perp", rule_id=rule,
        start=config.start, end=config.end, profile=binance_gate_profile()), reader=store)
        for s, rule in RULES.items()}
    return candles, daily, data


def comparison_markdown(receipt):
    local = timezone(timedelta(hours=7))
    lines = ["# Five-coin Spot + BTC/ETH Perp research", "",
        "Research only. No activation/gate conclusion, annualization, or 24-month profitability claim.", "",
        f"Window UTC+7: {datetime.fromisoformat(receipt['window']['start']).astimezone(local).isoformat()} → "
        f"{datetime.fromisoformat(receipt['window']['end']).astimezone(local).isoformat()}",
        "1,000 USDT; Spot cap 60% (40/20/20/10/10); Perp notional cap 30% (BTC/ETH 50/50), 3x.",
        "Minimum new-entry reserve 10%; unused budgets stay in USDT. Spot marks between 4h boundaries are unobserved.", "",
        "| Portfolio | Daily loss | Net return | Known-cost PnL USDT | Observed DD | Spot trades | Perp trades | Funding complete |",
        "|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in receipt["results"]:
        s = r["summary"]
        net = f"{s['net_return_pct']:.6f}%" if s["net_return_pct"] is not None else "UNKNOWN"
        counts = {m: sum(v["closed_trades"] for k, v in s["contributions"].items() if k.startswith(m+":"))
                  for m in ("spot", "perp")}
        lines.append(f"| {r['variant']} | {r['daily_loss_pct']:g}% | {net} | {s['pnl_after_known_costs']:.6f} | "
            f"{s['max_drawdown_known_pct']:.6f}% | {counts['spot']} | {counts['perp']} | {s['funding_complete']} |")
    lines += ["", "## Interpretation", "",
        "Both loss configurations use DD 10%. Zero Perp trades means no qualifying recorded entries, not proven profitability.",
        "Compare only paired results on this same short window, not the earlier 24-month 65% Spot cap study.",
        "Reports include per-coin/market contributions, fees, signed funding, notional/margin, fills and blocked-entry reasons.",
        "Original source and frozen evidence checksums are unchanged; each published result was deterministically reproduced.", ""]
    lines += ["## Recorded Perp diagnostics", "",
        "These are the rules in this frozen snapshot, not a claim about today's deployed rules.", "",
        "| Coin | Entry signals | Threshold | Maximum entry confidence | At threshold | Quote gaps >45s | Maximum gap |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for symbol, d in receipt["perp_diagnostics"].items():
        maximum = f"{d['maximum_entry_confidence']*100:g}%" if d["maximum_entry_confidence"] is not None else "none"
        lines.append(f"| {symbol} | {d['entry_signals']} | {d['confidence_threshold']*100:g}% | {maximum} | "
            f"{d['entry_signals_at_threshold']} | {d['quote_gaps_over_45s']} | {d['maximum_quote_gap_seconds']:.3f}s |")
    lines.append("")
    return "\n".join(lines)


def run_study(database, report_root, *, start=START, end=END, collect_funding=False,
              loader=load_mixed_inputs, trend_filter=True, funding_histories=None,
              fetcher=fetch_funding_snapshot, progress=None):
    variants = [("Spot+Perp" if enabled else "Spot-only control", MixedConfig(start=start, end=end,
        include_perp=enabled, daily_loss=daily, trend_filter=trend_filter))
        for enabled in (True, False) for daily in (".03", ".05")]
    source = Path(database).expanduser().resolve()
    verified = verify_backup(source)
    before = file_hash(source)
    root = Path(report_root).expanduser().resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    evidence = create_backup(source, root/"evidence")
    frozen = Path(evidence["backup"])
    evidence_hash = file_hash(frozen)
    verify_backup(frozen)
    candles, daily, datasets = loader(IntradayStore(frozen, read_only=True), variants[0][1])
    validate_inputs(variants[0][1], candles, daily)
    histories, collection = dict(funding_histories or {}), []
    if collect_funding:
        for symbol in sorted(variants[0][1].perp_weights):
            try:
                snapshot = fetcher(symbol, start, end)
                saved = save_funding_snapshot(root, snapshot)
                histories[symbol] = read_funding_snapshot(root, saved["funding_id"]).history
                collection.append({"status": "verified", **saved})
            except (OSError, ValueError) as error:
                collection.append({"symbol": symbol, "status": "unavailable", "error_type": type(error).__name__})
    # This explicit copy also makes fixture/imported funding histories reproducible offline.
    _write(root/"funding-inputs.json", json.dumps({s: h.model_dump(mode="json") for s, h in sorted(histories.items())},
                                               indent=2, allow_nan=False))
    if progress:
        progress({"phase": "evidence_ready", "source_sha256": before, "funding": collection,
            "perp_inputs": {s: {"quotes": len(d.quotes), "verified_decisions": len(d.decisions)}
                            for s, d in sorted(datasets.items())}})
    results = []
    for name, config in variants:
        report = simulate_mixed(config, candles, daily, datasets, histories)
        report["inputs"]["evidence_sha256"] = evidence_hash
        report["inputs"]["funding_inputs_sha256"] = file_hash(root/"funding-inputs.json")
        saved = publish_report(root/"reports", report)
        loaded = read_report(root/"reports", saved["run_id"])
        restored = MixedConfig.model_validate({k: loaded["config"][k] for k in MixedConfig.model_fields})
        repeated = simulate_mixed(restored, candles, daily, datasets, histories)
        if loaded["result_id"] != repeated["result_id"] or loaded["summary"] != repeated["summary"]:
            raise ValueError("mixed portfolio publication or deterministic replay mismatch")
        for name_series in SERIES:
            digest = hashlib.sha256()
            for row in repeated[name_series]:
                digest.update((encoded(row)+"\n").encode())
            if file_hash(Path(saved["report_directory"])/(name_series+".jsonl")) != digest.hexdigest():
                raise ValueError("published mixed ledger series differ from deterministic replay")
        results.append({"variant": name, "daily_loss_pct": float(config.daily_loss*100),
            "run_id": saved["run_id"], "result_id": report["result_id"], "status": report["status"],
            "dataset_checksum": report["inputs"]["dataset_checksum"], "summary": report["summary"],
            "limitations": report["limitations"], "deterministic_rerun_verified": True})
        if progress:
            progress({"phase": "replay_complete", **results[-1]})
    if file_hash(source) != before or file_hash(frozen) != evidence_hash:
        raise ValueError("immutable mixed research evidence changed")
    if len({r["result_id"] for r in results}) != 4 or len({r["dataset_checksum"] for r in results}) != 1:
        raise ValueError("mixed study requires four distinct configs on one shared dataset")
    receipt = {"research_only": True, "activation_allowed": False, "official_gate_eligible": False,
        "window": {"start": start.isoformat(), "end": end.isoformat()}, "original_source": str(source),
        "source_manifest": verified, "source_sha256": before, "source_unchanged": True,
        "evidence": evidence, "evidence_unchanged": True, "collection": collection,
        "funding_inputs_sha256": file_hash(root/"funding-inputs.json"),
        "perp_diagnostics": perp_diagnostics(datasets), "results": results}
    _write(root/"comparison.json", json.dumps(receipt, indent=2, allow_nan=False))
    _write(root/"comparison.md", comparison_markdown(receipt))
    return receipt


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Offline paired Spot + Perp research (recorded Jev or historical quant); no activation")
    parser.add_argument("--database", required=True, help="Verified immutable SQLite backup with matching manifest")
    parser.add_argument("--report-root", required=True, help="New private report directory, never overwritten")
    parser.add_argument("--collect-funding", action="store_true", help="Collect and freeze public historical funding")
    parser.add_argument("--mode", choices=("recorded-jev", "historical-quant"), default="recorded-jev",
                        help="Recorded Jev window (default) or separate 24-month deterministic Perp study")
    parser.add_argument("--collect-perp", action="store_true", help="Explicitly collect public native Futures candles, marks and funding")
    parser.add_argument("--perp-inputs", help="Immutable historical Futures input bundle for offline reproduction")
    parser.add_argument("--collect-trailing", action="store_true", help="Collect native H1/M30 contract bars for trailing-cadence research only")
    parser.add_argument("--trailing-inputs", help="Frozen H1/M30 supplemental bundle for offline trailing-cadence reproduction")
    parser.add_argument("--collect-intraday", action="store_true", help="Collect native H1/H4/H8 context and M15 contract/mark inputs")
    parser.add_argument("--intraday-inputs", help="Frozen intraday bundle for offline three-case reproduction")
    parser.add_argument("--reuse-perp-inputs", help="During intraday collection: reuse frozen H4 history; collect only missing warmup")
    parser.add_argument("--reuse-trailing-inputs", help="During intraday collection: reuse frozen H1 history; collect only missing warmup")
    parser.add_argument("--baseline-reference", help="Existing A report directory; require exact control identity/summary/three journals")
    parser.add_argument("--start", type=datetime.fromisoformat, help="Cadence/timeframe only: aware ISO start; intraday defaults 2024-10-29 UTC")
    parser.add_argument("--end", type=datetime.fromisoformat, help="Cadence/timeframe only: aware ISO end, default 2026-10-02 UTC")
    parser.add_argument("--preset", choices=("baseline", "stop-extension", "perp-daily-policy", "perp-daily-compounding", "perp-realized-trailing", "perp-trailing-cadence", "perp-intraday-timeframes"),
                        help="Historical-only matrix: baseline (6), stop-extension (8), perp-daily-policy (9), perp-daily-compounding (4), perp-realized-trailing (6), perp-trailing-cadence (3), or perp-intraday-timeframes (3)")
    parser.add_argument("--drawdown-policy", choices=("terminal", "observe-only", "initial-capital"),
                        help="Historical only: peak DD halt (default), observe DD only, or terminal loss from initial capital")
    args = parser.parse_args(argv)
    progress = lambda item: print(json.dumps(item, allow_nan=False), flush=True)
    if (args.collect_trailing or args.trailing_inputs) and (
        args.mode != "historical-quant" or args.preset != "perp-trailing-cadence"):
        parser.error("trailing inputs require historical-quant --preset perp-trailing-cadence")
    if (args.start is not None or args.end is not None) and (args.mode != 'historical-quant' or
        args.preset not in {'perp-trailing-cadence', 'perp-intraday-timeframes'}):
        parser.error('custom window requires a historical cadence or intraday-timeframe preset')
    if (args.collect_intraday or args.intraday_inputs or args.reuse_perp_inputs or args.reuse_trailing_inputs or
        args.baseline_reference) and (args.mode != 'historical-quant' or args.preset != 'perp-intraday-timeframes'):
        parser.error('intraday inputs require historical-quant --preset perp-intraday-timeframes')
    if args.mode == "historical-quant":
        if args.collect_funding or args.collect_perp == bool(args.perp_inputs):
            parser.error("historical-quant requires exactly one of --collect-perp / --perp-inputs; not --collect-funding")
        if args.preset == 'perp-intraday-timeframes':
            if args.collect_perp or args.collect_intraday == bool(args.intraday_inputs) or args.drawdown_policy not in (None, 'observe-only'):
                parser.error('intraday study requires frozen --perp-inputs, one --collect-intraday / --intraday-inputs and observe-only DD')
            if (args.reuse_perp_inputs or args.reuse_trailing_inputs) and not args.collect_intraday:
                parser.error('reuse paths require --collect-intraday')
            from intraday.replay_v2.intraday_study import run_study as run_intraday
            receipt = run_intraday(args.database, args.report_root, inputs_path=args.perp_inputs,
                collect_intraday=args.collect_intraday, intraday_inputs_path=args.intraday_inputs,
                reuse_perp_path=args.reuse_perp_inputs, reuse_trailing_path=args.reuse_trailing_inputs,
                baseline_reference=args.baseline_reference, progress=progress,
                **{k:v for k,v in {'start':args.start, 'end':args.end}.items() if v is not None})
        elif args.preset == "perp-trailing-cadence":
            if args.collect_trailing == bool(args.trailing_inputs) or args.drawdown_policy not in (None, 'observe-only'):
                parser.error("cadence study requires exactly one --collect-trailing / --trailing-inputs and observe-only DD")
            from intraday.replay_v2.trailing_cadence_study import run_study as run_cadence
            receipt = run_cadence(args.database, args.report_root, collect_perp=args.collect_perp,
                inputs_path=args.perp_inputs, collect_trailing=args.collect_trailing,
                trailing_inputs_path=args.trailing_inputs, progress=progress,
                **{k:v for k,v in {'start': args.start, 'end': args.end}.items() if v is not None})
        else:
            from intraday.replay_v2.historical_study import run_study as run_historical
            receipt = run_historical(args.database, args.report_root, collect_perp=args.collect_perp,
                                     inputs_path=args.perp_inputs, progress=progress, preset=args.preset or "baseline",
                                     **({'drawdown_policy': args.drawdown_policy} if args.drawdown_policy is not None else {}))
    else:
        if args.collect_perp or args.perp_inputs or args.preset or args.drawdown_policy is not None:
            parser.error("--collect-perp / --perp-inputs / --preset / --drawdown-policy require --mode historical-quant")
        receipt = run_study(args.database, args.report_root, collect_funding=args.collect_funding, progress=progress)
    print(json.dumps({"comparison": str(Path(args.report_root).expanduser()/"comparison.md"),
                      "runs": len(receipt["results"]), "source_unchanged": receipt["source_unchanged"]}))


if __name__ == "__main__":
    main()
