"""Command line entry point for the standalone intraday paper system."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intraday.config import IntradayConfig
from intraday.contracts import Direction, ProviderKind, ProviderProfile, ProviderRole
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
from intraday.provider_profiles import ProviderSecretStore
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
    provider = commands.add_parser("provider")
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)
    for name in ("add", "list", "show", "remove"):
        command = provider_commands.add_parser(name)
        if name in {"add", "show", "remove"}:
            command.add_argument("profile_id")
        command.add_argument("--database", default=None)
        command.add_argument("--secrets-file", default=None)
    add = provider_commands.choices["add"]
    add.add_argument("--role", required=True, choices=[item.value for item in ProviderRole])
    add.add_argument("--kind", required=True, choices=[item.value for item in ProviderKind])
    add.add_argument("--base-url")
    add.add_argument("--model", required=True)
    add.add_argument("--api-key-stdin", action="store_true")
    add.add_argument("--replace", action="store_true")
    return parser


def _default_secrets_file() -> Path:
    config_home = os.getenv("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "aigorithmic-trading" / "provider-secrets.toml"


def _provider_cli(arguments) -> None:
    database = Path(
        arguments.database
        or os.getenv("INTRADAY_DATABASE", "state/intraday/intraday.sqlite3")
    )
    secret_store = ProviderSecretStore(arguments.secrets_file or _default_secrets_file())
    store = IntradayStore(database)
    command = arguments.provider_command

    if command == "add":
        kind = ProviderKind(arguments.kind)
        role = ProviderRole(arguments.role)
        base_url = arguments.base_url
        if kind == ProviderKind.OPENROUTER_DECISIONS:
            base_url = base_url or "https://openrouter.ai/api/alpha/decisions"
        elif not base_url:
            raise SystemExit("--base-url is required for OpenAI-compatible providers")
        if arguments.api_key_stdin:
            api_key = sys.stdin.readline().rstrip("\r\n")
        else:
            api_key = getpass.getpass("Provider API key: ")
        now = datetime.now(timezone.utc)
        existing = store.provider_profile(arguments.profile_id)
        if existing is not None and not arguments.replace:
            raise SystemExit("provider profile already exists; pass --replace to update it")
        profile = ProviderProfile.create(
            profile_id=arguments.profile_id,
            role=role,
            kind=kind,
            base_url=base_url,
            model=arguments.model,
            credential_version=secrets.token_hex(16),
            created_at=existing.created_at if existing and arguments.replace else now,
            updated_at=now,
        )
        secret_store.upsert(profile, api_key, replace=arguments.replace)
        store.sync_provider_profile(profile)
        print(
            json.dumps(
                {**profile.model_dump(mode="json"), "has_secret": True}, indent=2
            )
        )
        return

    if command == "list":
        secret_ids = {profile.profile_id for profile in secret_store.list_profiles()}
        profiles = [
            {**profile, "has_secret": profile["profile_id"] in secret_ids}
            for profile in store.list_provider_profiles()
        ]
        print(json.dumps(profiles, indent=2))
        return

    profile = store.provider_profile(arguments.profile_id)
    if profile is None:
        raise SystemExit("unknown provider profile")
    if command == "show":
        try:
            secret_store.get(arguments.profile_id)
            has_secret = True
        except KeyError:
            has_secret = False
        metadata = next(
            item
            for item in store.list_provider_profiles()
            if item["profile_id"] == arguments.profile_id
        )
        print(json.dumps({**metadata, "has_secret": has_secret}, indent=2))
        return

    for role in ProviderRole:
        assignment = store.provider_assignment(role)
        if assignment and assignment["profile_id"] == arguments.profile_id:
            raise SystemExit(
                f"provider profile is active for {role.value}; deactivate it first"
            )
    secret_store.remove(arguments.profile_id)
    store.delete_provider_profile(arguments.profile_id)
    print(json.dumps({"profile_id": arguments.profile_id, "removed": True}, indent=2))


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
    if arguments.command == "provider":
        _provider_cli(arguments)
        return
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
