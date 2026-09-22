"""Command line entry point for the standalone intraday paper system."""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from intraday.config import IntradayConfig
from intraday.contracts import Direction
from intraday.cross_venue import (
    CrossVenuePolicy,
    derive_cross_venue_thresholds,
    enrich_with_cross_venue,
)
from intraday.cross_venue_evaluation import (
    CrossVenueEvaluationEvidence,
    evaluate_cross_venue_promotion,
)
from intraday.hyperliquid import HyperliquidFeed
from intraday.market import BinanceUsdMClient
from intraday.notifications import TelegramNotifier, drain_outbox
from intraday.replay import compare_cross_venue
from intraday.runtime import run_news_cycle, run_once
from intraday.store import IntradayStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="intraday")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "doctor", "collect", "news", "run", "cross-venue-status",
        "cross-venue-replay", "cross-venue-evaluate",
    ):
        command = commands.add_parser(name)
        command.add_argument("--database", default=None)
    run = commands.choices["run"]
    run.add_argument("--direction", choices=[item.value for item in Direction], default="Hold")
    run.add_argument("--once", action="store_true")
    commands.choices["cross-venue-replay"].add_argument(
        "--output-dir", default="state/intraday/cross-venue-replay"
    )
    commands.choices["cross-venue-evaluate"].add_argument("--evidence", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--database", default=None)
    return parser


def _doctor(config: IntradayConfig) -> dict:
    store = IntradayStore(config.database)
    return {
        "status": "ok",
        "mode": config.mode,
        "provider": config.provider,
        "execution_enabled": False,
        "symbol": config.symbol,
        "market": "Binance USD-M perpetual",
        "margin_mode": "isolated",
        "leverage": 3,
        "news_enabled": config.news_enabled,
        "cross_venue_mode": config.cross_venue_mode,
        "hyperliquid_enabled": config.hyperliquid_enabled,
        "cross_venue_activation_allowed": store.cross_venue_activation_allowed(),
        "telegram_enabled": config.telegram_enabled,
        "database": str(store.database),
    }


def _news_loop(config: IntradayConfig) -> None:
    store = IntradayStore(config.database)
    while True:
        result = run_news_cycle(store)
        print(json.dumps({"news_cycle": result}), flush=True)
        time.sleep(config.news_interval_seconds)


def main() -> None:
    arguments = _parser().parse_args()
    config = IntradayConfig.from_environment(database=arguments.database)
    if arguments.command == "doctor":
        print(json.dumps(_doctor(config), indent=2))
        return
    if arguments.command == "serve":
        import uvicorn

        from intraday.web import create_app

        uvicorn.run(
            create_app(
                database=config.database,
                control_token=config.control_token,
                cross_venue_mode=config.cross_venue_mode,
            ),
            host=config.dashboard_host,
            port=config.dashboard_port,
        )
        return
    if arguments.command == "news":
        print(json.dumps(run_news_cycle(IntradayStore(config.database)), indent=2))
        return
    if arguments.command == "cross-venue-status":
        store = IntradayStore(config.database)
        result = store.venue_health("hyperliquid")
        latest = store.latest_cross_venue_evaluation()
        result["latest_evaluation"] = latest.model_dump(mode="json") if latest else None
        print(json.dumps(result, indent=2))
        return
    if arguments.command == "cross-venue-replay":
        store = IntradayStore(config.database)
        snapshots = store.list_snapshots()
        decisions = store.list_recorded_decisions()
        if not snapshots or len(decisions) != len(snapshots):
            raise SystemExit("replay requires one recorded decision per snapshot")
        comparison = compare_cross_venue(
            snapshots, decisions, database_dir=arguments.output_dir,
            initial_equity=config.initial_equity,
        )
        print(json.dumps(asdict(comparison), indent=2))
        return
    if arguments.command == "cross-venue-evaluate":
        with open(arguments.evidence, encoding="utf-8") as handle:
            evidence = CrossVenueEvaluationEvidence.model_validate(json.load(handle))
        evaluation = evaluate_cross_venue_promotion(evidence)
        IntradayStore(config.database).record_cross_venue_evaluation(evaluation)
        print(evaluation.model_dump_json(indent=2))
        return

    client = BinanceUsdMClient()
    if arguments.command == "collect":
        snapshot = client.snapshot(config.symbol)
        print(snapshot.model_dump_json(indent=2))
        return

    direction = Direction(arguments.direction)
    store = IntradayStore(config.database)
    if config.cross_venue_mode == "active" and not store.cross_venue_activation_allowed():
        raise SystemExit("cross-venue active mode requires a recorded promote evaluation")
    if config.news_enabled and not arguments.once:
        threading.Thread(target=_news_loop, args=(config,), daemon=True).start()
    notifier = (
        TelegramNotifier(token=config.telegram_bot_token, chat_id=config.telegram_chat_id)
        if config.telegram_enabled else None
    )
    hyperliquid = None
    if config.hyperliquid_enabled:
        hyperliquid = HyperliquidFeed(
            metadata_interval_seconds=config.hyperliquid_metadata_interval_seconds
        )
        hyperliquid.start()
    cross_venue_policy = CrossVenuePolicy()
    next_threshold_refresh = datetime.min.replace(tzinfo=timezone.utc)
    last_retention_date = None
    while True:
        try:
            now = datetime.now(timezone.utc)
            snapshot = client.snapshot(config.symbol, now=now)
            if hyperliquid is not None:
                store = IntradayStore(config.database)
                if last_retention_date != now.date():
                    store.prune_venue_frames(before=now - timedelta(days=30))
                    last_retention_date = now.date()
                frame = hyperliquid.latest_frame(now=now)
                if frame is not None:
                    store.record_venue_frame(frame)
                history = store.list_venue_frames("hyperliquid", limit=1000)
                snapshot = enrich_with_cross_venue(snapshot, frame, history=history)
                if now >= next_threshold_refresh:
                    threshold_history = store.list_venue_frames(
                        "hyperliquid", limit=300_000
                    )
                    thresholds = derive_cross_venue_thresholds(
                        threshold_history, now=now
                    )
                    cross_venue_policy = CrossVenuePolicy(thresholds=thresholds)
                    next_threshold_refresh = now + timedelta(minutes=30)
            result = run_once(
                database=config.database,
                snapshot=snapshot,
                direction=direction,
                initial_equity=config.initial_equity,
                cross_venue_mode=config.cross_venue_mode,
                cross_venue_policy=cross_venue_policy,
                now=now,
            )
            print(json.dumps(result), flush=True)
            if notifier is not None and result["notifications_enabled"]:
                delivery = drain_outbox(IntradayStore(config.database), notifier)
                if delivery["delivered"] or delivery["failed"]:
                    print(json.dumps({"telegram": delivery}), flush=True)
        except Exception as error:
            print(json.dumps({"status": "degraded", "error": type(error).__name__}), flush=True)
        if arguments.once:
            return
        time.sleep(config.interval_seconds)


if __name__ == "__main__":
    main()
