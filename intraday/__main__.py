"""Command line entry point for the standalone intraday paper system."""

from __future__ import annotations

import argparse
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
from zoneinfo import ZoneInfo

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
from intraday.decision_experiments import (
    make_experiment_pair_id,
    record_compact_shadow,
)
from intraday.decision_evaluation import (
    evaluate_compact_experiment,
    generate_retrospective,
)
from intraday import deployment as deployment_cli
from intraday.hyperliquid import HyperliquidFeed
from intraday.journal import (
    count_training_candidates,
    export_training_data,
    training_output_path,
)
from intraday.market import BinanceUsdMClient, MultiCadenceMarketCache, StaleMarketData
from intraday.notifications import TelegramNotifier, drain_outbox
from intraday.outcomes import evaluate_pending_outcomes
from intraday.provider_client import ProviderPreflightClient
from intraday.provider_connect import _masked_api_key, connect_provider
from intraday.provider_profiles import ProviderSecretStore
from intraday.providers import AssignedDecisionProvider, StubDecisionProvider
from intraday.portfolio_coordinator import (
    ParentPortfolioState,
    apply_operator_command,
)
from intraday.portfolio_soak import (
    evaluate_portfolio_soak,
    run_soak_cycle,
    run_spot_soak_observation,
)
from intraday.parent_runtime import (
    flatten_parent_paper_positions,
    run_parent_risk_cycle,
    run_parent_paper_cycle,
)
from intraday.replay import compare_cross_venue
from intraday.runtime import (
    process_pending_commands,
    run_analysis_cycle,
    run_news_cycle,
    run_once,
    run_rule_proposal_cycle,
)
from intraday.store import IntradayStore
from intraday.spot_signal import BinanceSpotDailyClient, evaluate_donchian
from intraday.scheduler import claim_cadence
from intraday.scoped_rule_lifecycle import (
    activate_scoped_rule,
    evaluate_scoped_replay,
    evaluate_scoped_soak,
    start_scoped_rule_soak,
)


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
    setup = commands.add_parser("setup")
    setup.add_argument("--project-root", type=Path, default=Path.cwd())
    setup.add_argument("--with-hermes", action="store_true")
    for name in ("start", "stop", "restart"):
        commands.add_parser(name)
    logs = commands.add_parser("logs")
    logs.add_argument("--tail", type=int, default=200)
    logs.add_argument("--no-follow", action="store_true")
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
    activate = provider_commands.add_parser("activate")
    activate.add_argument("role", choices=[item.value for item in ProviderRole])
    activate.add_argument("profile_id")
    activate.add_argument("--database", default=None)
    activate.add_argument("--secrets-file", default=None)
    deactivate = provider_commands.add_parser("deactivate")
    deactivate.add_argument("role", choices=[item.value for item in ProviderRole])
    deactivate.add_argument("--database", default=None)
    deactivate.add_argument("--secrets-file", default=None)
    connect = commands.add_parser("connect")
    connect_roles = connect.add_subparsers(dest="connect_role", required=True)
    connect_jev = connect_roles.add_parser("jev")
    connect_jev.add_argument(
        "provider_option",
        nargs="?",
        choices=("openrouter", "typesafe", "custom-provider"),
    )
    connect_llm = connect_roles.add_parser("llm")
    connect_llm.add_argument(
        "provider_option",
        nargs="?",
        choices=("anthropic-compatible", "openai-compatible"),
    )
    for command in (connect_jev, connect_llm):
        command.add_argument("--database", default=None)
        command.add_argument("--secrets-file", default=None)
        command.add_argument("--api-key-stdin", action="store_true")
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
    experiment = portfolio_commands.add_parser("experiment")
    experiment_commands = experiment.add_subparsers(
        dest="experiment_command", required=True
    )
    experiment_status = experiment_commands.add_parser("status")
    experiment_status.add_argument("--database", default=None)
    experiment_evaluate = experiment_commands.add_parser("evaluate")
    experiment_evaluate.add_argument("--database", default=None)
    experiment_evaluate.add_argument(
        "--scope", choices=["all", *(scope.value for scope in DecisionScope)],
        default="all",
    )
    retrospective = portfolio_commands.add_parser("retrospective")
    retrospective_commands = retrospective.add_subparsers(
        dest="retrospective_command", required=True
    )
    retrospective_run = retrospective_commands.add_parser("run")
    retrospective_run.add_argument("--database", default=None)
    retrospective_run.add_argument("--date", default=None)
    retrospective_run.add_argument("--once", action="store_true")
    rules = portfolio_commands.add_parser("rules")
    rules_commands = rules.add_subparsers(dest="rules_command", required=True)
    rules_status = rules_commands.add_parser("status")
    rules_status.add_argument("--database", default=None)
    for name in ("replay", "start-soak", "evaluate", "activate"):
        command = rules_commands.add_parser(name)
        command.add_argument("candidate_id")
        command.add_argument("--database", default=None)
        if name == "activate":
            command.add_argument("--evaluation-id", required=True)
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
            api_key = _masked_api_key("Provider API key: ")
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
        "schema_version": store.schema_version(),
        "cadences_seconds": {
            "risk": config.risk_interval_seconds,
            "order_book": config.order_book_interval_seconds,
            "perp_numeric": config.perp_decision_interval_seconds,
            "derivatives": config.derivatives_interval_seconds,
            "compact_shadow": config.compact_shadow_interval_seconds,
            "llm_analysis": config.llm_analysis_interval_seconds,
        },
        "retrospective": {
            "hour": config.retrospective_hour_vietnam,
            "timezone": "Asia/Ho_Chi_Minh",
        },
        "scheduler": store.latest_scheduler_runs(),
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
    if command == "experiment":
        if arguments.experiment_command == "status":
            result = {}
            for scope in DecisionScope:
                latest = store.latest_decision_experiment_evaluation(scope)
                result[scope.value] = {
                    "summary": store.decision_experiment_pair_summary(scope),
                    "latest_evaluation": (
                        latest.model_dump(mode="json") if latest else None
                    ),
                    "auto_activation": False,
                }
            print(json.dumps(result, indent=2))
            return
        scopes = (
            tuple(DecisionScope)
            if arguments.scope == "all"
            else (DecisionScope(arguments.scope),)
        )
        evaluated_at = datetime.now(timezone.utc)
        evaluations = [
            evaluate_compact_experiment(store, scope=scope, evaluated_at=evaluated_at)
            for scope in scopes
        ]
        print(json.dumps([item.model_dump(mode="json") for item in evaluations], indent=2))
        return
    if command == "retrospective":
        generated_at = datetime.now(timezone.utc)
        report_date = (
            datetime.fromisoformat(arguments.date).date()
            if arguments.date
            else generated_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
            - timedelta(days=1)
        )
        print(json.dumps(generate_retrospective(
            store, report_date=report_date, generated_at=generated_at
        ), indent=2))
        return
    if command == "rules":
        now = datetime.now(timezone.utc)
        if arguments.rules_command == "status":
            payload = {
                scope.value: {
                    "registry": store.scoped_rule_registry(scope),
                    "rules": store.list_scoped_rules(scope),
                }
                for scope in DecisionScope
            }
            print(json.dumps(payload, indent=2))
            return
        try:
            if arguments.rules_command == "replay":
                result = evaluate_scoped_replay(
                    store, arguments.candidate_id, now=now
                ).model_dump(mode="json")
            elif arguments.rules_command == "start-soak":
                result = start_scoped_rule_soak(
                    store, arguments.candidate_id, now=now
                )
            elif arguments.rules_command == "evaluate":
                result = evaluate_scoped_soak(
                    store, arguments.candidate_id, now=now
                ).model_dump(mode="json")
            else:
                result = activate_scoped_rule(
                    store, arguments.candidate_id,
                    evaluation_id=arguments.evaluation_id, now=now,
                )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
        return
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
            market = MultiCadenceMarketCache(BinanceUsdMClient())
            spot_market = BinanceSpotDailyClient()
            config = IntradayConfig.from_environment(database=arguments.database)
            interval = config.risk_interval_seconds
            background_started = False
            while True:
                now = datetime.now(timezone.utc)
                process_pending_commands(store, now=now)
                try:
                    snapshot = market.snapshot("BTCUSDT", now=now)
                except StaleMarketData as error:
                    print(json.dumps({"status": "degraded", "error": str(error)}), flush=True)
                    if arguments.once:
                        return
                    time.sleep(max(1, interval))
                    continue
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
                slot = claim_cadence(
                    store, "portfolio_soak_perp", now,
                    config.perp_decision_interval_seconds,
                )
                result = {"status": "waiting_for_next_slot"}
                spot_slot = claim_cadence(
                    store, "portfolio_soak_spot", now, 86_400
                )
                if spot_slot is not None:
                    try:
                        spot_rule = store.load_active_scoped_rule(
                            DecisionScope.SPOT_DAILY
                        )
                        daily = None
                        if spot_rule is not None:
                            limit = max(
                                spot_rule.parameters.entry_window,
                                spot_rule.parameters.exit_window,
                                spot_rule.parameters.atr_period,
                            ) + 2
                            daily = spot_market.candles(
                                limit=max(35, limit), now=now
                            )
                        result["spot_daily"] = run_spot_soak_observation(
                            store,
                            provider,
                            snapshot,
                            now=now,
                            rule=spot_rule,
                            candles=daily,
                        )
                    except Exception as error:
                        store.record_portfolio_soak_tick(
                            scope=DecisionScope.SPOT_DAILY,
                            status="gate_error",
                            created_at=now,
                        )
                        result["spot_daily"] = "gate_error"
                        store.finish_scheduler_run(
                            "portfolio_soak_spot",
                            spot_slot,
                            status="error",
                            error_code=type(error).__name__,
                            finished_at=now,
                        )
                    else:
                        store.finish_scheduler_run(
                            "portfolio_soak_spot",
                            spot_slot,
                            status="success",
                            finished_at=now,
                        )
                if slot is not None:
                    try:
                        perp_result = run_soak_cycle(
                            store, provider, snapshot, now=now,
                            scopes=(DecisionScope.PERP_INTRADAY,),
                        )
                        result.update(perp_result)
                    except Exception as error:
                        store.finish_scheduler_run(
                            "portfolio_soak_perp", slot, status="error",
                            error_code=type(error).__name__, finished_at=now,
                        )
                        raise
                    else:
                        store.finish_scheduler_run(
                            "portfolio_soak_perp", slot, status="success", finished_at=now
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
        market = MultiCadenceMarketCache(BinanceUsdMClient())
        spot_market = BinanceSpotDailyClient()
        config = IntradayConfig.from_environment(database=arguments.database)
        interval = config.risk_interval_seconds
        if not arguments.once:
            if config.news_enabled:
                threading.Thread(
                    target=_news_loop, args=(config,), daemon=True
                ).start()
            threading.Thread(
                target=_analysis_loop, args=(config,), daemon=True
            ).start()
        cached_daily_close = None
        last_spot_check_day = None
        spot_observation = None
        while True:
            now = datetime.now(timezone.utc)
            try:
                snapshot = market.snapshot("BTCUSDT", now=now)
            except StaleMarketData as error:
                print(json.dumps({"status": "degraded", "error": str(error)}), flush=True)
                if arguments.once:
                    return
                time.sleep(max(1, interval))
                continue
            process_pending_commands(store, now=now, snapshot=snapshot)
            spot_event = False
            utc_day = now.astimezone(timezone.utc).date()
            if last_spot_check_day != utc_day:
                limit = max(
                    spot_rule.parameters.entry_window,
                    spot_rule.parameters.exit_window,
                    spot_rule.parameters.atr_period,
                ) + 2
                daily = spot_market.candles(limit=max(35, limit), now=now)
                if daily:
                    closed_at = datetime.fromtimestamp(
                        int(daily[-1][6]) / 1000, tz=timezone.utc
                    )
                    if cached_daily_close != closed_at:
                        spot_observation = evaluate_donchian(
                            daily, spot_rule.parameters
                        )
                        cached_daily_close = closed_at
                        spot_event = True
                last_spot_check_day = utc_day
            if spot_observation is None:
                raise RuntimeError("no closed daily candle is available")
            risk_result = run_parent_risk_cycle(
                store, snapshot, spot_observation,
                spot_rule=spot_rule, perp_rule=perp_rule, now=now,
            )
            slots = {}
            experiment_pairs = {}
            compact_jobs = {}
            perp_slot = claim_cadence(
                store, "paper_perp_numeric", now,
                config.perp_decision_interval_seconds,
            )
            if perp_slot is not None:
                slots[DecisionScope.PERP_INTRADAY] = (
                    "paper_perp_numeric", perp_slot
                )
                compact_slot = claim_cadence(
                    store, "paper_perp_compact_shadow", now,
                    config.compact_shadow_interval_seconds,
                )
                if compact_slot is not None:
                    experiment_pairs[DecisionScope.PERP_INTRADAY] = (
                        make_experiment_pair_id(
                            DecisionScope.PERP_INTRADAY, snapshot, compact_slot
                        )
                    )
                    compact_jobs[DecisionScope.PERP_INTRADAY] = compact_slot
            if spot_event and spot_observation.entry:
                if store.claim_scheduler_run(
                    "paper_spot_daily", cached_daily_close, started_at=now
                ):
                    slots[DecisionScope.SPOT_DAILY] = (
                        "paper_spot_daily", cached_daily_close
                    )
                    experiment_pairs[DecisionScope.SPOT_DAILY] = (
                        make_experiment_pair_id(
                            DecisionScope.SPOT_DAILY, snapshot, cached_daily_close
                        )
                    )
            result = risk_result
            if slots:
                try:
                    result = run_parent_paper_cycle(
                        store, provider, snapshot, spot_observation,
                        spot_rule=spot_rule, perp_rule=perp_rule,
                        decision_scopes=tuple(slots),
                        experiment_pair_ids=experiment_pairs,
                        now=now,
                    )
                except Exception as error:
                    for job, slot in slots.values():
                        store.finish_scheduler_run(
                            job, slot, status="error",
                            error_code=type(error).__name__, finished_at=now,
                        )
                    raise
                else:
                    for job, slot in slots.values():
                        store.finish_scheduler_run(
                            job, slot, status="success", finished_at=now
                        )
                shadow_errors = []
                for scope, pair_id in experiment_pairs.items():
                    rule = spot_rule if scope == DecisionScope.SPOT_DAILY else perp_rule
                    try:
                        record_compact_shadow(
                            store, provider, snapshot, scope=scope,
                            rule_id=rule.rule_id, experiment_pair_id=pair_id, now=now,
                        )
                    except Exception as error:
                        shadow_errors.append(f"{scope.value}:{type(error).__name__}")
                        if scope in compact_jobs:
                            store.finish_scheduler_run(
                                "paper_perp_compact_shadow", compact_jobs[scope],
                                status="error", error_code=type(error).__name__,
                                finished_at=now,
                            )
                    else:
                        if scope in compact_jobs:
                            store.finish_scheduler_run(
                                "paper_perp_compact_shadow", compact_jobs[scope],
                                status="success", finished_at=now,
                            )
                result["shadow_errors"] = shadow_errors
            result["risk_fills"] = risk_result["fills"]
            result["decision_scopes"] = [scope.value for scope in slots]
            outcome_slot = claim_cadence(store, "signal_outcomes", now, 60)
            if outcome_slot is not None:
                try:
                    result["outcomes"] = evaluate_pending_outcomes(store, now=now)
                except Exception as error:
                    store.finish_scheduler_run(
                        "signal_outcomes", outcome_slot, status="error",
                        error_code=type(error).__name__, finished_at=now,
                    )
                else:
                    store.finish_scheduler_run(
                        "signal_outcomes", outcome_slot, status="success", finished_at=now
                    )
            vietnam_now = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
            retrospective_local = vietnam_now.replace(
                hour=config.retrospective_hour_vietnam,
                minute=0, second=0, microsecond=0,
            )
            if vietnam_now >= retrospective_local:
                retrospective_slot = retrospective_local.astimezone(timezone.utc)
                if store.claim_scheduler_run(
                    "daily_retrospective", retrospective_slot, started_at=now
                ):
                    try:
                        result["retrospective"] = generate_retrospective(
                            store,
                            report_date=vietnam_now.date() - timedelta(days=1),
                            generated_at=now,
                        )["report_id"]
                        for scope in DecisionScope:
                            evaluate_compact_experiment(
                                store, scope=scope, evaluated_at=now
                            )
                            challenger_id = store.scoped_rule_registry(scope).get(
                                "challenger_id"
                            )
                            if challenger_id:
                                evaluate_scoped_soak(
                                    store, challenger_id, now=now
                                )
                        result["rule_proposal"] = run_rule_proposal_cycle(
                            store,
                            ProviderSecretStore(config.provider_secrets_file),
                            now=now,
                        )
                    except Exception as error:
                        store.finish_scheduler_run(
                            "daily_retrospective", retrospective_slot,
                            status="error", error_code=type(error).__name__,
                            finished_at=now,
                        )
                    else:
                        store.finish_scheduler_run(
                            "daily_retrospective", retrospective_slot,
                            status="success", finished_at=now,
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
    if arguments.command == "setup":
        try:
            result = deployment_cli.bootstrap(
                arguments.project_root,
                with_hermes=arguments.with_hermes,
            )
        except (ValueError, PermissionError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
        return
    raw_arguments = sys.argv[1:]
    if arguments.command in {"start", "stop", "restart", "logs"}:
        deployment = deployment_cli.load_deployment()
        if deployment is None:
            raise SystemExit("no global deployment is registered; run aigt setup")
        command = deployment_cli.service_command(
            deployment,
            arguments.command,
            tail=getattr(arguments, "tail", 200),
            follow=not getattr(arguments, "no_follow", False),
        )
        code = deployment_cli.execute(command, cwd=deployment.project_root)
        if code:
            raise SystemExit(code)
        return
    explicit_native_paths = any(
        item in raw_arguments for item in ("--database", "--secrets-file")
    )
    if arguments.command in {
        "analysis", "doctor", "status", "provider", "connect"
    } and not explicit_native_paths:
        deployment = deployment_cli.load_deployment()
        if deployment is not None:
            code = deployment_cli.execute(
                deployment_cli.admin_command(deployment, raw_arguments),
                cwd=deployment.project_root,
            )
            if code:
                raise SystemExit(code)
            return
    if arguments.command == "migrate-state":
        print(json.dumps(_migrate_state(arguments.source, arguments.database), indent=2))
        return
    if arguments.command == "provider":
        _provider_cli(arguments)
        return
    if arguments.command == "connect":
        connect_provider(
            arguments,
            database=resolve_database_path(arguments.database),
            secrets_file=Path(arguments.secrets_file or _default_secrets_file()),
        )
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
                operator_read_token=config.operator_read_token,
                operator_action_token=config.operator_action_token,
                operator_actions_enabled=config.operator_actions_enabled,
                operator_request_ttl_seconds=config.operator_request_ttl_seconds,
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
