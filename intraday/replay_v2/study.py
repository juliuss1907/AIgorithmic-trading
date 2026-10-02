"""Isolated account-replay studies. Never registers rules or starts campaigns."""

import calendar
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import os
import sqlite3

from intraday.assets import ticker_symbol
from intraday.backups import create_backup
from intraday.contracts import DecisionScope, ScopedRuleCandidate
from intraday.replay_v2.artifacts import publish_report
from intraday.replay_v2.contracts import ReplayConfig, binance_gate_profile, utc
from intraday.replay_v2.data import load_dataset
from intraday.replay_v2.engine import simulate
from intraday.replay_v2.gates import _audit_entries
from intraday.replay_v2.metrics import fingerprint
from intraday.store import IntradayStore


STUDY_COINS = ("ETHUSDT", "NEARUSDT", "ZECUSDT", "SOLUSDT")


def months_before(at, count):
    at = utc(at)
    year, month = divmod(at.year*12 + at.month-1-count, 12)
    return at.replace(year=year, month=month+1,
                      day=min(at.day, calendar.monthrange(year, month+1)[1]))


def spot_windows(now):
    end = utc(now).replace(hour=utc(now).hour//4*4, minute=0, second=0, microsecond=0)
    return months_before(end, 24), months_before(end, 6), end


def snapshot_source(source, directory, *, now):
    directory = Path(directory).resolve()
    backup = create_backup(source, directory/"baseline", now=now)
    working = directory/"research.sqlite3"
    fd = os.open(working, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with sqlite3.connect(Path(backup["backup"]).as_uri()+"?mode=ro", uri=True) as src:
        with sqlite3.connect(working) as target:
            src.backup(target)
    return {"baseline": backup["backup"], "research_database": str(working),
            "sha256": backup["sha256"], "integrity": backup["integrity"]}


def base_rule(store, symbol, scope):
    rule = (store.load_active_scoped_rule(scope, symbol=symbol)
            or store.load_scoped_challenger(scope, symbol=symbol))
    if rule:
        return rule
    rows = store.list_scoped_rules(scope, symbol=symbol)
    return store.load_scoped_rule(rows[0]["id"]) if rows else None


def variant_rule(base, changes, *, now, model_ref="research/deterministic"):
    parameters = type(base.parameters).model_validate({**base.parameters.model_dump(), **changes})
    payload = base.model_dump(exclude={"content_hash"})
    payload.update(parameters=parameters, created_at=utc(now), parent_rule_id=base.rule_id,
                   model_ref=model_ref, thesis_id="replay-study", prompt_version="replay-study-v1",
                   rationale="Isolated research variant; not registered or approved for execution.")
    payload["rule_id"] = f"{base.symbol.lower()}-research-{fingerprint(parameters.model_dump(mode='json'))[:20]}"
    return ScopedRuleCandidate.create(**payload)


def economic_status(report):
    summary = report["summary"]
    waiting, rejected = [], []
    if summary.get("closed_trades", 0) < 6:
        waiting.append("minimum_6_closed_trades")
    if summary.get("net_return_pct") is None:
        waiting.append("net_return_unknown")
    elif summary["net_return_pct"] <= 0:
        rejected.append("nonpositive_net_return")
    drawdown = summary.get("max_drawdown_known_pct")
    if drawdown is None:
        waiting.append("drawdown_unknown")
    elif drawdown >= 8:
        rejected.append("max_drawdown_at_least_8pct")
    if _audit_entries(report):
        rejected.append("hard_risk_violation")
    gaps = ("history_gap", "history_starts_after", "history_ends_before", "no_valid_", "unverified_recorded")
    waiting.extend(code for code in report["limitations"] if any(part in code for part in gaps))
    return ("reject" if rejected else "deferred" if waiting else "pass"), waiting+rejected


def select_training(items):
    good = [item for item in items if item["status"] == "pass"
            and item["summary"]["closed_trades"] >= 6
            and item["summary"]["net_return_pct"] is not None
            and item["summary"]["net_return_pct"] > 0
            and item["summary"]["max_drawdown_known_pct"] < 8]
    return max(good, key=lambda item: (
        item["summary"]["net_return_pct"]-item["summary"]["max_drawdown_known_pct"],
        -item["summary"]["max_drawdown_known_pct"], item.get("id", "")), default=None)


def run_variant(data, config, rule, root, *, now):
    config = config.model_copy(update={"rule_id": rule.rule_id})
    data = replace(data, rule=rule)
    report = simulate(config, data)
    saved = publish_report(root, report, now=now)
    status, blockers = economic_status(report)
    return {"id": rule.rule_id, "run_id": saved["run_id"], "status": status,
            "blockers": blockers, "summary": report["summary"],
            "parameters": rule.parameters.model_dump(mode="json"),
            "rule": rule.model_dump(mode="json"), "dataset_checksum": report["inputs"]["dataset_checksum"]}


def study_spot(database, symbol, root, *, now, collect=False, client=None):
    symbol = ticker_symbol(symbol)
    start, split, end = spot_windows(now)
    store = IntradayStore(database, read_only=not collect)
    rule = base_rule(store, symbol, DecisionScope.SPOT_4H)
    result = {"symbol": symbol, "market": "spot", "status": "deferred", "blockers": [],
              "window": {"start":start.isoformat(), "split":split.isoformat(), "end":end.isoformat()},
              "training": [], "holdout": None, "research_only": True, "activation_allowed": False}
    if not rule:
        result["blockers"] = ["no_registered_spot_rule"]
        return result
    if collect:
        if client is None:
            from intraday.spot_signal import BinanceSpotDailyClient
            client = BinanceSpotDailyClient(asset_catalog=store.asset_catalog())
        rows = client.backfill(symbol=symbol, interval="4h", start_time=int((start-timedelta(days=11)).timestamp()*1000),
                               end_time=int(end.timestamp()*1000)-1, now=now)
        store.record_asset_candles(symbol, "4h", rows)
    reader = IntradayStore(database, read_only=True)
    with reader.read_snapshot():
        rows = reader.list_asset_candles(symbol, "4h", as_of=end)
        ms = int(start.timestamp()*1000)
        requested = [row for row in rows if ms <= int(row[0]) < int(end.timestamp()*1000)]
        expected = int((end-start).total_seconds()/14400)
        coverage = len(requested)/expected
        warmup = [row for row in rows if int(row[0]) < ms]
        if not requested or int(requested[0][0]) > ms or coverage < .99 or len(warmup) < 61:
            result["blockers"] = ["minimum_24_month_history"]
            result["coverage"] = coverage
            return result
        config = ReplayConfig(symbol=symbol, market="spot", rule_id=rule.rule_id,
                              start=start, end=split, profile=binance_gate_profile())
        data = load_dataset(database, config, reader=reader)
        changes = [{}]+[{"entry_window":entry, "exit_window":8, "atr_period":14}
                        for entry in ((30,) if symbol == "ETHUSDT" else (30,40,50))]
        seen = set()
        for change in changes:
            variant = variant_rule(rule, change, now=now) if change else rule
            digest = fingerprint(variant.parameters.model_dump(mode="json"))
            if digest in seen:
                continue
            seen.add(digest)
            result["training"].append(run_variant(data, config, variant, root, now=now))
        chosen = select_training(result["training"])
        if chosen:
            variant = ScopedRuleCandidate.model_validate(chosen["rule"])
            hold_config = config.model_copy(update={"start":split, "end":end})
            hold_data = load_dataset(database, hold_config, reader=reader)
            result["selected_rule"] = chosen["id"]
            result["holdout"] = run_variant(hold_data, hold_config, variant, root, now=now)
            result["status"] = result["holdout"]["status"]
            result["blockers"] = result["holdout"]["blockers"]
        else:
            result["blockers"] = ["no_passing_training_candidate"]
            result["status"] = "reject" if all(r["status"] == "reject" for r in result["training"]) else "deferred"
    return result
