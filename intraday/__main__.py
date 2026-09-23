"""Command line entry point for the standalone intraday paper system."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from intraday.config import (
    IntradayConfig,
    default_provider_secrets_path,
    resolve_database_path,
)
from intraday.contracts import (
    DecisionScope,
    Direction,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
)
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
from intraday.journal import (
    count_training_candidates,
    export_training_data,
    training_output_path,
)
from intraday.market import BinanceUsdMClient
from intraday.notifications import TelegramNotifier, drain_outbox
from intraday.provider_client import ProviderPreflightClient
from intraday.provider_profiles import ProviderSecretStore
from intraday.providers import AssignedDecisionProvider, StubDecisionProvider
from intraday.portfolio_coordinator import (
    ParentPortfolioState,
    apply_operator_command,
)
from intraday.portfolio_soak import evaluate_portfolio_soak, run_soak_cycle
from intraday.parent_runtime import (
    flatten_parent_paper_positions,
    run_parent_paper_cycle,
)
from intraday.replay import compare_cross_venue
from intraday.runtime import (
    process_pending_commands,
    run_analysis_cycle,
    run_news_cycle,
    run_once,
)
from intraday.store import IntradayStore
from intraday.spot_signal import BinanceSpotDailyClient, evaluate_donchian


def _project_version() -> str:
    try:
        return version("system-trading-lab")
    except PackageNotFoundError:
        return "0.1.0"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aigt")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {_project_version()}"
    )
    commands = parser.add_subparsers(dest="command")
    for name in (
        "doctor", "status", "collect", "news", "analysis", "run", "cross-venue-status",
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
    migrate_state = commands.add_parser("migrate-state")
    migrate_state.add_argument("--from", dest="source", required=True)
    migrate_state.add_argument("--database", default=None)
    provider = commands.add_parser("provider")
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)
    for name in ("add", "list", "show", "remove", "test"):
        command = provider_commands.add_parser(name)
        if name in {"add", "show", "remove", "test"}:
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
    setup = provider_commands.add_parser("setup")
    setup.add_argument("--database", default=None)
    setup.add_argument("--secrets-file", default=None)
    setup.add_argument("--api-key-stdin", action="store_true")
    activate = provider_commands.add_parser("activate")
    activate.add_argument("role", choices=[item.value for item in ProviderRole])
    activate.add_argument("profile_id")
    activate.add_argument("--database", default=None)
    activate.add_argument("--secrets-file", default=None)
    deactivate = provider_commands.add_parser("deactivate")
    deactivate.add_argument("role", choices=[item.value for item in ProviderRole])
    deactivate.add_argument("--database", default=None)
    deactivate.add_argument("--secrets-file", default=None)
    portfolio = commands.add_parser("portfolio")
    portfolio_commands = portfolio.add_subparsers(
        dest="portfolio_command", required=True
    )
    for name in ("status", "pause", "resume", "flatten"):
        command = portfolio_commands.add_parser(name)
        command.add_argument("--database", default=None)
    soak = portfolio_commands.add_parser("soak")
    soak_commands = soak.add_subparsers(dest="soak_command", required=True)
    soak_evaluate = soak_commands.add_parser("evaluate")
    soak_evaluate.add_argument("--database", default=None)
    soak_evaluate.add_argument("--at", default=None)
    soak_run = soak_commands.add_parser("run")
    soak_run.add_argument("--database", default=None)
    soak_run.add_argument("--secrets-file", default=None)
    soak_run.add_argument("--once", action="store_true")
    activate_paper = portfolio_commands.add_parser("activate-paper")
    activate_paper.add_argument("--evaluation-id", required=True)
    activate_paper.add_argument("--database", default=None)
    paper = portfolio_commands.add_parser("paper")
    paper_commands = paper.add_subparsers(dest="paper_command", required=True)
    paper_run = paper_commands.add_parser("run")
    paper_run.add_argument("--database", default=None)
    paper_run.add_argument("--secrets-file", default=None)
    paper_run.add_argument("--once", action="store_true")
    journal = commands.add_parser("journal")
    journal_commands = journal.add_subparsers(
        dest="journal_command", required=True
    )
    journal_export = journal_commands.add_parser("export")
    journal_export.add_argument("--database", default=None)
    journal_export.add_argument("--output-dir", default="training_data")
    journal_export.add_argument("--min-pnl-pct", type=float, default=0.5)
    journal_export.add_argument("--max-pnl-pct", type=float, default=-0.5)
    return parser


def _default_secrets_file() -> Path:
    configured = os.getenv("INTRADAY_PROVIDER_SECRETS_FILE")
    return Path(configured).expanduser() if configured else default_provider_secrets_path()


def _provider_cli(arguments) -> None:
    database = resolve_database_path(arguments.database)
    secret_store = ProviderSecretStore(arguments.secrets_file or _default_secrets_file())
    store = IntradayStore(database)
    command = arguments.provider_command

    if command == "setup":
        try:
            role = ProviderRole(input("Role [jev/llm]: ").strip().lower())
        except ValueError as error:
            raise SystemExit("role must be jev or llm") from error
        allowed = {
            ProviderRole.JEV: (
                ProviderKind.TYPESAFE_SYSTEMONE,
                ProviderKind.OPENROUTER_DECISIONS,
            ),
            ProviderRole.LLM: (
                ProviderKind.OPENAI_COMPATIBLE,
                ProviderKind.ANTHROPIC_MESSAGES,
            ),
        }[role]
        default_kind = allowed[0]
        kind_value = input(
            f"Protocol [{'/'.join(item.value for item in allowed)}] "
            f"(default {default_kind.value}): "
        ).strip() or default_kind.value
        try:
            kind = ProviderKind(kind_value)
        except ValueError as error:
            raise SystemExit("unsupported provider protocol") from error
        if kind not in allowed:
            raise SystemExit(f"{kind.value} cannot be used for the {role.value} role")
        profile_id = input("Profile id: ").strip()
        model_default = "jev-latest" if kind == ProviderKind.TYPESAFE_SYSTEMONE else ""
        model = input(
            f"Model{f' (default {model_default})' if model_default else ''}: "
        ).strip() or model_default
        if not model:
            raise SystemExit("model is required")
        endpoints = {
            ProviderKind.TYPESAFE_SYSTEMONE: "https://api.typesafe.ai/v1/systemone",
            ProviderKind.OPENROUTER_DECISIONS: "https://openrouter.ai/api/alpha/decisions",
            ProviderKind.ANTHROPIC_MESSAGES: "https://api.anthropic.com/v1/messages",
        }
        base_url = endpoints.get(kind)
        if base_url is None:
            base_url = input(
                "OpenAI-compatible base URL "
                "(for example https://api.openai.com/v1): "
            ).strip()
        if arguments.api_key_stdin:
            api_key = sys.stdin.readline().rstrip("\r\n")
        else:
            api_key = getpass.getpass("Provider API key: ")
        now = datetime.now(timezone.utc)
        if store.provider_profile(profile_id) is not None:
            raise SystemExit("provider profile already exists")
        profile = ProviderProfile.create(
            profile_id=profile_id,
            role=role,
            kind=kind,
            base_url=base_url,
            model=model,
            credential_version=secrets.token_hex(16),
            created_at=now,
            updated_at=now,
        )
        secret_store.upsert(profile, api_key, replace=False)
        store.sync_provider_profile(profile)
        result = ProviderPreflightClient().test(secret_store.get(profile_id))
        store.record_provider_test(
            profile_id,
            status=result.status,
            tested_at=now,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
        )
        active = False
        if result.status == "ok":
            store.activate_provider(role, profile_id, actor="cli", now=now)
            active = True
        print(
            json.dumps(
                {
                    "profile_id": profile_id,
                    "role": role.value,
                    "kind": kind.value,
                    "status": result.status,
                    "latency_ms": result.latency_ms,
                    "active": active,
                },
                indent=2,
            )
        )
        if not active:
            raise SystemExit(1)
        return

    if command == "add":
        kind = ProviderKind(arguments.kind)
        role = ProviderRole(arguments.role)
        base_url = arguments.base_url
        if kind == ProviderKind.OPENROUTER_DECISIONS:
            base_url = base_url or "https://openrouter.ai/api/alpha/decisions"
        elif kind == ProviderKind.TYPESAFE_SYSTEMONE:
            base_url = base_url or "https://api.typesafe.ai/v1/systemone"
        elif kind == ProviderKind.ANTHROPIC_MESSAGES:
            base_url = base_url or "https://api.anthropic.com/v1/messages"
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

    if command == "activate":
        assignment = store.activate_provider(
            ProviderRole(arguments.role),
            arguments.profile_id,
            actor="cli",
            now=datetime.now(timezone.utc),
        )
        print(json.dumps({**assignment, "active": True}, indent=2))
        return

    if command == "deactivate":
        role = ProviderRole(arguments.role)
        store.deactivate_provider(role)
        print(json.dumps({"role": role.value, "active": False}, indent=2))
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

    if command == "test":
        result = ProviderPreflightClient().test(secret_store.get(arguments.profile_id))
        store.record_provider_test(
            arguments.profile_id,
            status=result.status,
            tested_at=datetime.now(timezone.utc),
            latency_ms=result.latency_ms,
            error_code=result.error_code,
        )
        output = {
            "profile_id": arguments.profile_id,
            "status": result.status,
            "latency_ms": result.latency_ms,
            "error_code": result.error_code,
            "provider_request_id": result.provider_request_id,
        }
        print(json.dumps(output, indent=2))
        if result.status != "ok":
            raise SystemExit(1)
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


def _migrate_state(source_value: str, database_value: str | None) -> dict[str, str]:
    source = Path(source_value).expanduser().resolve(strict=True)
    database = resolve_database_path(database_value).resolve()
    if source == database:
        raise SystemExit("source and destination database must be different")
    if database.exists():
        raise SystemExit(f"destination database already exists: {database}")

    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = database.with_name(f".{database.name}.{uuid.uuid4().hex}.tmp")
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_connection:
            integrity = source_connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise SystemExit(f"source database integrity check failed: {integrity}")
            with sqlite3.connect(temporary) as destination_connection:
                source_connection.backup(destination_connection)
                copied_integrity = destination_connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                if copied_integrity != "ok":
                    raise SystemExit(
                        f"copied database integrity check failed: {copied_integrity}"
                    )
        temporary.chmod(0o600)
        try:
            os.link(temporary, database)
        except FileExistsError as error:
            raise SystemExit(f"destination database already exists: {database}") from error
    except sqlite3.DatabaseError as error:
        raise SystemExit(f"database migration failed: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "status": "copied",
        "source": str(source),
        "database": str(database),
        "integrity": "ok",
    }


def _doctor(config: IntradayConfig) -> dict:
    store = IntradayStore(config.database)
    active_jev = store.provider_assignment(ProviderRole.JEV)
    active_llm = store.provider_assignment(ProviderRole.LLM)
    return {
        "status": "ok",
        "mode": config.mode,
        "provider": config.provider,
        "active_providers": {
            "jev": active_jev["profile_id"] if active_jev else None,
            "llm": active_llm["profile_id"] if active_llm else None,
        },
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


def _parent_portfolio_payload(state) -> dict:
    equity = state.equity
    spot_notional = state.spot_notional
    perp_notional = state.perp_notional
    return {
        "mode": "paper",
        "paper_active": state.paper_active,
        "equity": equity,
        "daily_return": state.daily_return,
        "drawdown": state.drawdown,
        "spot_notional": spot_notional,
        "perp_notional": perp_notional,
        "gross_exposure_pct": (spot_notional + abs(perp_notional)) / equity,
        "net_delta_pct": (spot_notional + perp_notional) / equity,
        "isolated_margin_pct": abs(perp_notional) / 3 / equity,
        "leverage": 3,
        "margin_mode": "isolated",
        "entries_paused": state.entries_paused,
        "halt_reason": state.halt_reason,
        "soak_evaluation_id": state.soak_evaluation_id,
        "updated_at": state.updated_at.isoformat(),
    }


def _portfolio_cli(arguments) -> None:
    store = IntradayStore(resolve_database_path(arguments.database))
    command = arguments.portfolio_command
    if command == "soak":
        if arguments.soak_command == "run":
            secret_store = ProviderSecretStore(
                arguments.secrets_file or _default_secrets_file()
            )
            provider = AssignedDecisionProvider(
                store,
                secret_store,
                fallback=StubDecisionProvider(direction=Direction.HOLD),
            )
            market = BinanceUsdMClient()
            config = IntradayConfig.from_environment(database=arguments.database)
            interval = config.interval_seconds
            background_started = False
            while True:
                now = datetime.now(timezone.utc)
                process_pending_commands(store, now=now)
                snapshot = market.snapshot("BTCUSDT", now=now)
                if store.load_parent_portfolio_state() is None:
                    initial = ParentPortfolioState(
                        mark_price=float(snapshot.features["mark_price"]),
                        day_start_equity=10_000,
                        high_water_mark=10_000,
                        entries_paused=True,
                        halt_reason="soak_not_promoted",
                        paper_active=False,
                        updated_at=now,
                    )
                    store.save_parent_portfolio_state(
                        initial, event_kind="initialized", actor="soak_worker"
                    )
                result = run_soak_cycle(
                    store, provider, snapshot, now=now
                )
                print(json.dumps(result), flush=True)
                if arguments.once:
                    return
                if not background_started:
                    if config.news_enabled:
                        threading.Thread(
                            target=_news_loop, args=(config,), daemon=True
                        ).start()
                    threading.Thread(
                        target=_analysis_loop, args=(config,), daemon=True
                    ).start()
                    background_started = True
                time.sleep(max(1, interval))
        evaluated_at = (
            datetime.fromisoformat(arguments.at)
            if arguments.at
            else datetime.now(timezone.utc)
        )
        evaluation = evaluate_portfolio_soak(
            store.list_portfolio_soak_ticks(), evaluated_at=evaluated_at
        )
        store.record_portfolio_soak_evaluation(evaluation)
        print(evaluation.model_dump_json(indent=2))
        return
    if command == "paper":
        state = store.load_parent_portfolio_state()
        if state is None or not state.paper_active:
            raise SystemExit("paper worker requires a promoted parent portfolio")
        spot_rule = store.load_active_scoped_rule(DecisionScope.SPOT_DAILY)
        perp_rule = store.load_active_scoped_rule(DecisionScope.PERP_INTRADAY)
        if spot_rule is None or perp_rule is None:
            raise SystemExit("paper worker requires both scoped champion rules")
        secret_store = ProviderSecretStore(
            arguments.secrets_file or _default_secrets_file()
        )
        provider = AssignedDecisionProvider(
            store,
            secret_store,
            fallback=StubDecisionProvider(direction=Direction.HOLD),
        )
        market = BinanceUsdMClient()
        spot_market = BinanceSpotDailyClient()
        config = IntradayConfig.from_environment(database=arguments.database)
        interval = config.interval_seconds
        if not arguments.once:
            if config.news_enabled:
                threading.Thread(
                    target=_news_loop, args=(config,), daemon=True
                ).start()
            threading.Thread(
                target=_analysis_loop, args=(config,), daemon=True
            ).start()
        cached_day = None
        spot_observation = None
        while True:
            now = datetime.now(timezone.utc)
            snapshot = market.snapshot("BTCUSDT", now=now)
            process_pending_commands(store, now=now, snapshot=snapshot)
            if cached_day != now.date() or spot_observation is None:
                limit = max(
                    spot_rule.parameters.entry_window,
                    spot_rule.parameters.exit_window,
                    spot_rule.parameters.atr_period,
                ) + 2
                daily = spot_market.candles(limit=max(35, limit), now=now)
                spot_observation = evaluate_donchian(
                    daily, spot_rule.parameters
                )
                cached_day = now.date()
            result = run_parent_paper_cycle(
                store,
                provider,
                snapshot,
                spot_observation,
                spot_rule=spot_rule,
                perp_rule=perp_rule,
                now=now,
            )
            print(json.dumps(result), flush=True)
            if arguments.once:
                return
            time.sleep(max(1, interval))
    state = store.load_parent_portfolio_state()
    if state is None:
        raise SystemExit("parent paper portfolio is not initialized")
    if command == "activate-paper":
        evaluation = store.portfolio_soak_evaluation(arguments.evaluation_id)
        latest_evaluation = store.latest_portfolio_soak_evaluation()
        if (
            evaluation is None
            or evaluation.status != "pass"
            or latest_evaluation is None
            or latest_evaluation.evaluation_id != evaluation.evaluation_id
        ):
            raise SystemExit("paper activation requires the exact id of a passing soak")
        now = datetime.now(timezone.utc)
        state = state.model_copy(
            update={
                "paper_active": True,
                "entries_paused": False,
                "halt_reason": None,
                "soak_evaluation_id": evaluation.evaluation_id,
                "updated_at": now,
            }
        )
        store.save_parent_portfolio_state(
            state, event_kind="activate_paper", actor="cli"
        )
        print(json.dumps(_parent_portfolio_payload(state), indent=2))
        return
    if command == "status":
        print(json.dumps(_parent_portfolio_payload(state), indent=2))
        return
    now = datetime.now(timezone.utc)
    try:
        if command == "flatten":
            state = flatten_parent_paper_positions(
                store, state, now=now, actor="cli"
            )
        else:
            state = apply_operator_command(state, command, now=now)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if command != "flatten":
        store.save_parent_portfolio_state(
            state, event_kind=command, actor="cli"
        )
    print(json.dumps(_parent_portfolio_payload(state), indent=2))


def _news_loop(config: IntradayConfig) -> None:
    store = IntradayStore(config.database)
    while True:
        result = run_news_cycle(store)
        print(json.dumps({"news_cycle": result}), flush=True)
        time.sleep(config.news_interval_seconds)


def _analysis_loop(config: IntradayConfig) -> None:
    store = IntradayStore(config.database)
    secret_store = ProviderSecretStore(config.provider_secrets_file)
    while True:
        result = run_analysis_cycle(store, secret_store)
        print(json.dumps({"analysis_cycle": result}), flush=True)
        if result["status"] == "skipped":
            time.sleep(min(60, config.llm_analysis_interval_seconds))
        else:
            time.sleep(config.llm_analysis_interval_seconds)


def _journal_cli(arguments) -> None:
    database = resolve_database_path(arguments.database)
    IntradayStore(database)
    now = datetime.now(timezone.utc)
    try:
        candidates = count_training_candidates(database)
        rows = export_training_data(
            arguments.min_pnl_pct,
            arguments.max_pnl_pct,
            database=database,
            output_dir=arguments.output_dir,
            now=now,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    hold = sum(row["answer"]["direction"] == "hold" for row in rows)
    result = {
        "output_file": str(
            training_output_path(arguments.output_dir, now).resolve()
        ),
        "exported": len(rows),
        "positive": len(rows) - hold,
        "hold": hold,
        "ambiguous_skipped": candidates - len(rows),
        "min_pnl_pct": arguments.min_pnl_pct,
        "max_pnl_pct": arguments.max_pnl_pct,
    }
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = _parser()
    arguments = parser.parse_args()
    if arguments.command is None:
        parser.print_help()
        return
    if arguments.command == "migrate-state":
        print(json.dumps(_migrate_state(arguments.source, arguments.database), indent=2))
        return
    if arguments.command == "provider":
        _provider_cli(arguments)
        return
    if arguments.command == "journal":
        _journal_cli(arguments)
        return
    if arguments.command == "portfolio":
        _portfolio_cli(arguments)
        return
    config = IntradayConfig.from_environment(database=arguments.database)
    if arguments.command == "doctor":
        print(json.dumps(_doctor(config), indent=2))
        return
    if arguments.command == "status":
        store = IntradayStore(config.database)
        result = _doctor(config)
        parent = store.load_parent_portfolio_state()
        result["portfolio"] = (
            _parent_portfolio_payload(parent) if parent is not None else None
        )
        print(json.dumps(result, indent=2))
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
    if arguments.command == "analysis":
        result = run_analysis_cycle(
            IntradayStore(config.database),
            ProviderSecretStore(config.provider_secrets_file),
        )
        print(json.dumps(result, indent=2))
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
    provider_secrets = ProviderSecretStore(config.provider_secrets_file)
    decision_provider = AssignedDecisionProvider(
        store,
        provider_secrets,
        fallback=StubDecisionProvider(direction=direction),
    )
    if config.cross_venue_mode == "active" and not store.cross_venue_activation_allowed():
        raise SystemExit("cross-venue active mode requires a recorded promote evaluation")
    if config.news_enabled and not arguments.once:
        threading.Thread(target=_news_loop, args=(config,), daemon=True).start()
    if not arguments.once:
        threading.Thread(target=_analysis_loop, args=(config,), daemon=True).start()
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
                decision_provider=decision_provider,
                secret_store=provider_secrets,
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
