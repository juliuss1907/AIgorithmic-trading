"""Command line entry point for the standalone intraday paper system."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import stat
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from intraday.assets import ASSET_REGISTRY, asset_spec
from intraday.asset_readiness import build_asset_readiness, format_asset_readiness
from intraday.replay_v2.cli import add_replay_parser, dispatch_replay, docker_replay_command, dispatch_gate_command
from intraday.llm_pipeline import active_llm_client
from intraday.asset_rule_lifecycle import (
    activate_asset_spot_rule,
    bootstrap_asset_spot_rule,
    evaluate_asset_spot_soak,
    propose_asset_spot_rule,
    replay_asset_spot_rule,
    start_asset_spot_soak,
)
from intraday.asset_auto_proposals import auto_propose_asset_rule
from intraday.spot_4h_lifecycle import (
    activate_spot_4h_rule,
    bootstrap_spot_4h_rule,
    evaluate_spot_4h_soak,
    refresh_spot_4h_history,
    replay_spot_4h_rule,
    start_spot_4h_soak,
)
from intraday.perp_bootstrap_lifecycle import (
    activate_perp_bootstrap,
    bootstrap_perp_rule,
    evaluate_perp_post_replay,
    replay_perp_bootstrap,
    start_perp_decision_soak,
)
from intraday.backups import create_backup, prepare_backup_directory, verify_backup
from intraday.config import (
    IntradayConfig,
    default_backup_directory,
    default_provider_secrets_path,
    resolve_database_path,
)
from intraday.contracts import (
    DecisionScope,
    Direction,
    ProviderKind,
    ProviderProfile,
    ProviderRole,
    SpotRuleParameters,
)
from intraday.cross_venue import (
    CrossVenuePolicy,
    HyperliquidFrameDataError,
    derive_cross_venue_thresholds,
    enrich_with_cross_venue,
)
from intraday.cross_venue_evaluation import (
    CrossVenueEvaluationEvidence,
    evaluate_cross_venue_promotion,
)
from intraday.dashboard_read_model import build_dashboard_snapshot
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
from intraday.external_context import (
    AsterCollector,
    AsterLiquidationFeed,
    CryptoRankCollector,
    LighterCollector,
    VariationalCollector,
    run_external_context_cycle,
)
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
from intraday.portfolio_view import latest_parent_market_view
from intraday.positions import build_positions_snapshot
from intraday.portfolio_soak import (
    CURRENT_SOAK_EVIDENCE_VERSION,
    evaluate_portfolio_soak,
    load_parent_soak_campaign_evidence,
    run_asset_lifecycle_observation,
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
from intraday.spot_signal import (
    BinanceSpotDailyClient,
    MultiCadenceSpotCache,
    MultiTimeframeSpotCache,
    evaluate_donchian,
)
from intraday.scheduler import claim_cadence
from intraday.scoped_rule_lifecycle import (
    activate_scoped_rule,
    evaluate_scoped_replay,
    evaluate_scoped_soak,
    start_scoped_rule_soak,
)
from intraday.soak_report import (
    build_soak_readiness_report,
    collect_database_metrics,
    prepare_report_output,
    serialize_report,
    write_report,
)
from intraday.upgrade_preflight import preflight_upgrade_backup
from intraday.execution.cli import add_execution_parser, dispatch_execution
from intraday.execution.connect import add_exchange_connect_parsers, dispatch_connect
from intraday.execution.perp_cli import add_perp_parser, dispatch_perp
from intraday.asset_onboarding import AssetOnboarding


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
    add_execution_parser(commands)
    add_perp_parser(commands)
    add_replay_parser(commands)
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
    serve.add_argument("--execution-database", type=Path, default=None, help="opt-in Perp controller journal; never exchange credentials")
    positions = commands.add_parser("positions")
    positions.add_argument("--database", default=None)
    assets = commands.add_parser("assets")
    asset_commands = assets.add_subparsers(dest="assets_command", required=True)
    assets_list = asset_commands.add_parser("list")
    assets_list.add_argument("--database", default=None)
    assets_status = asset_commands.add_parser("status")
    assets_status.add_argument("symbol", nargs="?")
    assets_status.add_argument("--database", default=None)
    readiness = asset_commands.add_parser("readiness", help="read-only scoped rule gates and venue state")
    readiness.add_argument("symbol", nargs="?")
    readiness.add_argument("--market", choices=["spot", "perp"])
    readiness.add_argument("--json", action="store_true", dest="json_output")
    readiness.add_argument("--database", default=None)
    for name in ("add", "scan"):
        command = asset_commands.add_parser(name, help="scope-specific Demo/Testnet onboarding")
        command.add_argument("symbol")
        command.add_argument("--market", required=True, choices=["spot", "perp"])
        command.add_argument("--notional", type=float, default=1000, help="size in each venue's quote currency")
        command.add_argument("--database", default=None)
        if name == "add":
            command.add_argument("--no-scan", action="store_true", help="register shadow scope without public scan")
    venue = asset_commands.add_parser("venue")
    venue_commands = venue.add_subparsers(dest="asset_venue_command", required=True)
    venue_set = venue_commands.add_parser("set")
    venue_set.add_argument("symbol")
    venue_set.add_argument("--market", required=True, choices=["spot", "perp"])
    venue_set.add_argument("--venue", required=True, choices=["bnb", "hl", "aster", "variational", "lighter"])
    venue_set.add_argument("--environment", required=True, choices=["demo", "testnet"])
    venue_set.add_argument("--scan-id")
    venue_set.add_argument("--database", default=None)
    assets_start_soak = asset_commands.add_parser("start-soak")
    assets_start_soak.add_argument("symbol")
    assets_start_soak.add_argument(
        "--scope", required=True, choices=[scope.value for scope in DecisionScope]
    )
    assets_start_soak.add_argument("--database", default=None)
    asset_rules = asset_commands.add_parser("rules")
    asset_rule_commands = asset_rules.add_subparsers(dest="asset_rules_command", required=True)
    asset_rule_status = asset_rule_commands.add_parser("status")
    asset_rule_status.add_argument("symbol")
    asset_rule_status.add_argument("--database", default=None)
    asset_rule_bootstrap = asset_rule_commands.add_parser("bootstrap")
    asset_rule_bootstrap.add_argument("symbol")
    asset_rule_bootstrap.add_argument("--scope", required=True, choices=["spot_daily", "spot_4h", "perp_intraday"])
    asset_rule_bootstrap.add_argument("--database", default=None)
    inherit_perp = asset_rule_commands.add_parser("inherit-perp", help="explicitly audit and inherit unchanged Perp champion collection")
    inherit_perp.add_argument("symbol")
    inherit_perp.add_argument("--collection-from", required=True, help="aware ISO timestamp of recorded collection start")
    inherit_perp.add_argument("--database", default=None)
    migrate_perp = asset_rule_commands.add_parser('migrate-perp', help='audit/select v2 without resetting collections')
    migrate_perp.add_argument('symbols', nargs='+')
    migrate_perp.add_argument('--dry-run', action='store_true')
    migrate_perp.add_argument('--database', default=None)
    automation = asset_rule_commands.add_parser('automation', help='opt-in weekly v2 gates; no auto activation')
    automation_commands = automation.add_subparsers(dest='automation_command', required=True)
    for name in ('set','pause','status'):
        command = automation_commands.add_parser(name)
        command.add_argument('--market', choices=('spot','perp','all'), default='all')
        command.add_argument('--database', default=None)
        if name == 'set':
            command.add_argument('--interval-days', type=int, choices=(7,), default=7)
    asset_rule_propose = asset_rule_commands.add_parser("propose")
    asset_rule_propose.add_argument("symbol")
    asset_rule_propose.add_argument("--scope", required=True, choices=["spot_daily"])
    asset_rule_propose.add_argument("--database", default=None)
    asset_rule_propose.add_argument("--secrets-file", default=None)
    for name in ("replay", "start-soak", "evaluate", "activate"):
        command = asset_rule_commands.add_parser(name)
        command.add_argument("candidate_id")
        command.add_argument("--database", default=None)
        command.add_argument("--report-dir", type=Path, default=None)
        if name == "replay":
            command.add_argument("--engine", choices=("v1","v2"), default=None)
        if name in {"replay","evaluate"}:
            command.add_argument("--funding-id", default=None)
        if name in {"start-soak", "activate"}:
            command.add_argument("--evaluation-id", required=name == "activate")
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
    backup = commands.add_parser("backup")
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_commands.add_parser("create")
    backup_create.add_argument("--output-dir", default=None)
    backup_create.add_argument("--database", default=None)
    backup_create.add_argument("--owner-uid", type=int, default=None, help=argparse.SUPPRESS)
    backup_create.add_argument("--owner-gid", type=int, default=None, help=argparse.SUPPRESS)
    backup_verify = backup_commands.add_parser("verify")
    backup_verify.add_argument("backup_path")
    upgrade = commands.add_parser("upgrade")
    upgrade_commands = upgrade.add_subparsers(dest="upgrade_command", required=True)
    upgrade_preflight = upgrade_commands.add_parser("preflight")
    upgrade_preflight.add_argument("--backup", required=True)
    upgrade_preflight.add_argument("--output")
    upgrade_preflight.add_argument("--work-dir")
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
    add_exchange_connect_parsers(connect_roles)
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
    soak_report = soak_commands.add_parser("report")
    soak_report.add_argument("--database", default=None)
    soak_report.add_argument("--output", default=None)
    soak_report.add_argument(
        "--owner-uid", type=int, default=None, help=argparse.SUPPRESS
    )
    soak_report.add_argument(
        "--owner-gid", type=int, default=None, help=argparse.SUPPRESS
    )
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


def _asset_payload(store: IntradayStore, symbol: str) -> dict:
    spec = store.asset_spec(symbol)
    lifecycles = store.list_asset_lifecycles(spec.symbol)
    return {
        "symbol": spec.symbol,
        "base_asset": spec.base_asset,
        "quote_asset": spec.quote_asset,
        "capability": spec.capability.value,
        "stages": {item.scope.value: item.stage.value for item in lifecycles},
        "execution_routes": AssetOnboarding(store).routes(spec.symbol),
        "venues": {
            "binance_spot": spec.binance_spot_symbol,
            "binance_perp": spec.binance_perp_symbol,
            "hyperliquid": spec.hyperliquid_coin,
            "aster": spec.aster_symbol,
            "variational": spec.variational_ticker,
            "lighter_market_id": spec.lighter_market_id,
        },
    }


def _assets_cli(arguments) -> None:
    if arguments.assets_command == 'rules' and arguments.asset_rules_command in {'migrate-perp','automation'}:
        from intraday.replay_v2.automation_cli import dispatch_automation
        dispatch_automation(arguments)
        return
    if arguments.assets_command == "readiness":
        try:
            report = build_asset_readiness(resolve_database_path(arguments.database),
                                          symbol=arguments.symbol, market=arguments.market)
        except (OSError, sqlite3.Error):
            raise SystemExit("Không thể đọc source DB; readiness không tạo hoặc migrate database.") from None
        except ValueError as error:
            raise SystemExit(str(error)) from None
        print(json.dumps(report, ensure_ascii=False, indent=2) if arguments.json_output
              else format_asset_readiness(report), flush=True)
        return
    store = IntradayStore(resolve_database_path(arguments.database))
    if arguments.assets_command in {"add", "scan", "venue"}:
        service = AssetOnboarding(store)
        now = datetime.now(timezone.utc)
        try:
            if arguments.assets_command == "venue":
                scan_id = arguments.scan_id or service.scan(arguments.symbol, market=arguments.market, now=now)["id"]
                result = service.select(arguments.symbol, market=arguments.market, venue=arguments.venue,
                                        environment=arguments.environment, scan_id=scan_id, now=datetime.now(timezone.utc))
            else:
                result = {}
                if arguments.assets_command == "add":
                    result["asset"] = service.add(arguments.symbol, market=arguments.market, now=now)
                if arguments.assets_command == "scan" or not arguments.no_scan:
                    scan = service.scan(arguments.symbol, market=arguments.market, now=now, notional=arguments.notional)
                    if arguments.assets_command == "scan":
                        result = scan
                    else:
                        result["scan"] = scan
                        if sys.stdin.isatty():
                            print(json.dumps(scan, indent=2), flush=True)
                            from prompt_toolkit import prompt
                            choice = prompt("Choose Binance Demo [bnb], or Enter to leave unselected: ").strip().lower()
                            if choice:
                                if choice != "bnb":
                                    raise ValueError("only verified Binance Demo execution can be selected")
                                result["route"] = service.select(arguments.symbol, market=arguments.market, venue="bnb",
                                                                environment="demo", scan_id=scan["id"], now=datetime.now(timezone.utc))
            print(json.dumps(result, indent=2), flush=True)
        except (ValueError, KeyError, TypeError) as error:
            raise SystemExit(str(error) if isinstance(error, ValueError) else "asset configuration unavailable") from None
        except (KeyboardInterrupt, EOFError):
            raise SystemExit("onboarding canceled; no trading was activated") from None
        return
    if arguments.assets_command == "list":
        result = [_asset_payload(store, symbol) for symbol in store.asset_catalog()]
    elif arguments.assets_command == "status":
        result = (
            _asset_payload(store, arguments.symbol)
            if arguments.symbol
            else [_asset_payload(store, symbol) for symbol in store.asset_catalog()]
        )
    elif arguments.assets_command == "rules":
        command = arguments.asset_rules_command
        now = datetime.now(timezone.utc)
        try:
            gate_result = dispatch_gate_command(store,arguments,now=now)
            if gate_result is not None:
                print(json.dumps(gate_result,indent=2,allow_nan=False))
                return
            if command == "status":
                from intraday.replay_v2.cli import gate_rule_status
                symbol = store.asset_spec(arguments.symbol).symbol
                result = {
                    scope.value: {
                        "registry": store.scoped_rule_registry(scope, symbol=symbol),
                        "rules": [
                            {**row, "replay": (
                                evaluation.model_dump(mode="json") if (evaluation := store.latest_scoped_rule_evaluation(row["id"], kind="replay")) else None
                            ), "soak": (
                                evaluation.model_dump(mode="json") if (evaluation := store.latest_scoped_rule_evaluation(row["id"], kind="soak")) else None
                            ), "gate_v2": gate_rule_status(store,row["id"])}
                            for row in store.list_scoped_rules(scope, symbol=symbol)
                        ],
                        "lifecycle": store.asset_lifecycle_record(symbol, scope),
                    }
                    for scope in DecisionScope
                }
            elif command == "bootstrap":
                bootstrap = {
                    "spot_daily": bootstrap_asset_spot_rule,
                    "spot_4h": bootstrap_spot_4h_rule,
                    "perp_intraday": bootstrap_perp_rule,
                }[arguments.scope]
                result = bootstrap(store, arguments.symbol, now=now).model_dump(mode="json")
            elif command == "propose":
                client = active_llm_client(
                    store, ProviderSecretStore(arguments.secrets_file or _default_secrets_file())
                )
                if client is None:
                    raise ValueError("no active LLM provider")
                result = propose_asset_spot_rule(store, arguments.symbol, client=client, now=now).model_dump(mode="json")
            elif command == "replay":
                candidate = store.load_scoped_rule(arguments.candidate_id)
                replay = (
                    replay_spot_4h_rule if candidate and candidate.scope is DecisionScope.SPOT_4H
                    else replay_perp_bootstrap if candidate and candidate.scope is DecisionScope.PERP_INTRADAY
                    else replay_asset_spot_rule
                )
                result = replay(store, arguments.candidate_id, now=now).model_dump(mode="json")
            elif command == "start-soak":
                candidate = store.load_scoped_rule(arguments.candidate_id)
                if candidate and candidate.scope is DecisionScope.PERP_INTRADAY:
                    result = start_perp_decision_soak(store, arguments.candidate_id, now=now)
                else:
                    if not arguments.evaluation_id:
                        raise ValueError("Spot start-soak requires --evaluation-id")
                    start = start_spot_4h_soak if candidate and candidate.scope is DecisionScope.SPOT_4H else start_asset_spot_soak
                    result = start(store, arguments.candidate_id,
                                   evaluation_id=arguments.evaluation_id, now=now)
            elif command == "evaluate":
                candidate = store.load_scoped_rule(arguments.candidate_id)
                evaluate = (
                    evaluate_spot_4h_soak if candidate and candidate.scope is DecisionScope.SPOT_4H
                    else evaluate_perp_post_replay if candidate and candidate.scope is DecisionScope.PERP_INTRADAY
                    else evaluate_asset_spot_soak
                )
                result = evaluate(store, arguments.candidate_id, now=now).model_dump(mode="json")
            else:
                candidate = store.load_scoped_rule(arguments.candidate_id)
                activate = (
                    activate_spot_4h_rule if candidate and candidate.scope is DecisionScope.SPOT_4H
                    else activate_perp_bootstrap if candidate and candidate.scope is DecisionScope.PERP_INTRADAY
                    else activate_asset_spot_rule
                )
                result = activate(store, arguments.candidate_id,
                                  evaluation_id=arguments.evaluation_id, now=now)
        except (ValueError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
    else:
        raise SystemExit("Use 'aigt assets rules start-soak CANDIDATE_ID --evaluation-id REPLAY_ID'")
    print(json.dumps(result, indent=2))


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
        "markets": {
            DecisionScope.PERP_INTRADAY.value: "Binance USD-M perpetual",
            DecisionScope.SPOT_DAILY.value: "Binance Spot",
        },
        "feature_schema_version": "2",
        "soak_evidence_version": CURRENT_SOAK_EVIDENCE_VERSION,
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
            "spot_quote": config.order_book_interval_seconds,
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
        "spot_price": state.spot_price,
        "perp_mark_price": state.perp_mark_price,
        "mark_price": state.mark_price,
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
    command = arguments.portfolio_command
    database = resolve_database_path(arguments.database)
    if (
        command == "soak"
        and arguments.soak_command == "report"
        and not database.is_file()
    ):
        raise SystemExit(f"database does not exist: {database}")
    store = IntradayStore(database)
    if command == "experiment":
        if arguments.experiment_command == "status":
            result = {}
            for scope in (DecisionScope.SPOT_DAILY, DecisionScope.PERP_INTRADAY):
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
            (DecisionScope.SPOT_DAILY, DecisionScope.PERP_INTRADAY)
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
        if arguments.soak_command == "report":
            try:
                snapshot = build_dashboard_snapshot(
                    store, now=datetime.now(timezone.utc)
                )
                report = build_soak_readiness_report(
                    snapshot, collect_database_metrics(store.database)
                )
                if arguments.output is not None:
                    write_report(
                        report,
                        arguments.output,
                        owner_uid=arguments.owner_uid,
                        owner_gid=arguments.owner_gid,
                    )
            except (
                FileExistsError,
                FileNotFoundError,
                PermissionError,
                ValueError,
                sqlite3.DatabaseError,
            ) as error:
                raise SystemExit(str(error)) from error
            sys.stdout.write(serialize_report(report))
            return
        if arguments.soak_command == "run":
            secret_store = ProviderSecretStore(
                arguments.secrets_file or _default_secrets_file()
            )
            provider = AssignedDecisionProvider(
                store,
                secret_store,
                fallback=StubDecisionProvider(direction=Direction.HOLD),
            )
            config = IntradayConfig.from_environment(database=arguments.database)
            market = MultiCadenceMarketCache(BinanceUsdMClient())
            spot_market = MultiCadenceSpotCache(
                BinanceSpotDailyClient(),
                quote_interval_seconds=config.order_book_interval_seconds,
            )
            btc_spot_4h_market = MultiTimeframeSpotCache(
                BinanceSpotDailyClient(),
                quote_interval_seconds=config.order_book_interval_seconds,
            )
            asset_perp_markets, asset_spot_markets = _new_asset_market_caches(config, store=store)
            interval = config.risk_interval_seconds
            background_started = False
            hyperliquid = (
                _start_portfolio_hyperliquid(config) if not arguments.once else None
            )
            while True:
                now = datetime.now(timezone.utc)
                try:
                    snapshot = market.snapshot("BTCUSDT", now=now)
                    spot_snapshot = spot_market.snapshot("BTCUSDT", now=now)
                except StaleMarketData as error:
                    print(json.dumps({"status": "degraded", "error": str(error)}), flush=True)
                    if arguments.once:
                        return
                    time.sleep(max(1, interval))
                    continue
                _record_portfolio_hyperliquid(
                    store, hyperliquid, now=now,
                    shadow=config.cross_venue_mode != "active",
                )
                if store.load_parent_portfolio_state() is None:
                    initial = ParentPortfolioState(
                        mark_price=float(snapshot.features["mark_price"]),
                        perp_mark_price=float(snapshot.features["mark_price"]),
                        spot_price=float(spot_snapshot.features["reference_price"]),
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
                process_pending_commands(
                    store,
                    now=now,
                    snapshot=snapshot,
                    spot_snapshot=spot_snapshot,
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
                            spot_snapshot = spot_market.snapshot(
                                "BTCUSDT", now=now, candle_limit=max(35, limit)
                            )
                            daily = spot_market.closed_candles()
                        result["spot_daily"] = run_spot_soak_observation(
                            store,
                            provider,
                            spot_snapshot,
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
                        store.record_snapshot(spot_snapshot)
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
                result["assets"] = _run_registered_asset_cycles(
                    store,
                    provider,
                    perp_markets=asset_perp_markets,
                    spot_markets=asset_spot_markets,
                    config=config,
                    now=now,
                )
                btc_4h_slot = claim_cadence(
                    store, "asset_spot_4h_btcusdt", now, 14_400,
                )
                if btc_4h_slot is not None:
                    try:
                        btc_4h_snapshot = btc_spot_4h_market.snapshot("BTCUSDT", now=now)
                        for candle_interval in ("4h", "8h", "1d"):
                            store.record_asset_candles(
                                "BTCUSDT", candle_interval,
                                btc_spot_4h_market.closed_candles(candle_interval),
                            )
                        candidate = (
                            store.load_scoped_challenger(DecisionScope.SPOT_4H)
                            or store.load_active_scoped_rule(DecisionScope.SPOT_4H)
                        )
                        result["btc_spot_4h"] = run_asset_lifecycle_observation(
                            store, provider, btc_4h_snapshot,
                            scope=DecisionScope.SPOT_4H, now=now,
                            spot_rule=candidate,
                            spot_candles=btc_spot_4h_market.closed_candles("4h"),
                        )
                    except Exception as error:
                        result["btc_spot_4h"] = f"error:{type(error).__name__}"
                        store.finish_scheduler_run(
                            "asset_spot_4h_btcusdt", btc_4h_slot,
                            status="error", error_code=type(error).__name__,
                            finished_at=datetime.now(timezone.utc),
                        )
                    else:
                        store.finish_scheduler_run(
                            "asset_spot_4h_btcusdt", btc_4h_slot,
                            status="success", finished_at=datetime.now(timezone.utc),
                        )
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
                            "signal_outcomes", outcome_slot, status="success",
                            finished_at=now,
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
                    if config.external_context_enabled:
                        threading.Thread(
                            target=_external_context_loop, args=(config,), daemon=True
                        ).start()
                    threading.Thread(
                        target=_asset_rule_bootstrap_loop, args=(config,), daemon=True
                    ).start()
                    threading.Thread(target=_gate_automation_loop,args=(config,),daemon=True).start()
                    background_started = True
                time.sleep(max(1, interval))
        evaluated_at = (
            datetime.fromisoformat(arguments.at)
            if arguments.at
            else datetime.now(timezone.utc)
        )
        _, ticks = load_parent_soak_campaign_evidence(store)
        evaluation = evaluate_portfolio_soak(ticks, evaluated_at=evaluated_at)
        store.record_portfolio_soak_evaluation(evaluation)
        print(evaluation.model_dump_json(indent=2))
        return
    if command == "paper":
        state = store.load_parent_portfolio_state()
        if state is None or not state.paper_active:
            raise SystemExit("paper worker requires a promoted parent portfolio")
        legacy_spot_rule = store.load_active_scoped_rule(DecisionScope.SPOT_DAILY)
        perp_rule = store.load_active_scoped_rule(DecisionScope.PERP_INTRADAY)
        if perp_rule is None:
            raise SystemExit("paper worker requires a Perp champion rule")
        legacy_spot_parameters = (
            legacy_spot_rule.parameters if legacy_spot_rule else SpotRuleParameters()
        )
        secret_store = ProviderSecretStore(
            arguments.secrets_file or _default_secrets_file()
        )
        provider = AssignedDecisionProvider(
            store,
            secret_store,
            fallback=StubDecisionProvider(direction=Direction.HOLD),
        )
        config = IntradayConfig.from_environment(database=arguments.database)
        market = MultiCadenceMarketCache(BinanceUsdMClient())
        spot_market = MultiCadenceSpotCache(
            BinanceSpotDailyClient(),
            quote_interval_seconds=config.order_book_interval_seconds,
        )
        spot_4h_market = MultiTimeframeSpotCache(
            BinanceSpotDailyClient(),
            quote_interval_seconds=config.order_book_interval_seconds,
        )
        asset_perp_markets, asset_spot_markets = _new_asset_market_caches(config, store=store)
        interval = config.risk_interval_seconds
        hyperliquid = (
            _start_portfolio_hyperliquid(config) if not arguments.once else None
        )
        if not arguments.once:
            if config.news_enabled:
                threading.Thread(
                    target=_news_loop, args=(config,), daemon=True
                ).start()
            threading.Thread(
                target=_analysis_loop, args=(config,), daemon=True
            ).start()
            if config.external_context_enabled:
                threading.Thread(
                    target=_external_context_loop, args=(config,), daemon=True
                ).start()
            threading.Thread(
                target=_asset_rule_bootstrap_loop, args=(config,), daemon=True
            ).start()
            threading.Thread(target=_gate_automation_loop,args=(config,),daemon=True).start()
        cached_daily_close = None
        last_spot_check_day = None
        legacy_spot_observation = None
        cached_4h_close = None
        spot_4h_observation = None
        while True:
            now = datetime.now(timezone.utc)
            try:
                snapshot = market.snapshot("BTCUSDT", now=now)
                spot_snapshot = spot_market.snapshot("BTCUSDT", now=now)
            except StaleMarketData as error:
                print(json.dumps({"status": "degraded", "error": str(error)}), flush=True)
                if arguments.once:
                    return
                time.sleep(max(1, interval))
                continue
            _record_portfolio_hyperliquid(
                store, hyperliquid, now=now,
                shadow=config.cross_venue_mode != "active",
            )
            asset_results = _run_registered_asset_cycles(
                store,
                provider,
                perp_markets=asset_perp_markets,
                spot_markets=asset_spot_markets,
                config=config,
                now=now,
            )
            process_pending_commands(
                store,
                now=now,
                snapshot=snapshot,
                spot_snapshot=spot_snapshot,
            )
            spot_4h_rule = store.load_active_scoped_rule(DecisionScope.SPOT_4H)
            spot_4h_snapshot = None
            spot_4h_event = False
            try:
                spot_4h_snapshot = spot_4h_market.snapshot("BTCUSDT", now=now)
                closed_4h = spot_4h_market.closed_candles("4h")
                if closed_4h:
                    close_at = int(closed_4h[-1][6])
                    if cached_4h_close != close_at:
                        for candle_interval in ("4h", "8h", "1d"):
                            store.record_asset_candles(
                                "BTCUSDT", candle_interval,
                                spot_4h_market.closed_candles(candle_interval),
                            )
                        if spot_4h_rule is not None:
                            spot_4h_observation = evaluate_donchian(
                                closed_4h, spot_4h_rule.parameters
                            )
                            spot_4h_event = True
                        cached_4h_close = close_at
                store.record_snapshot(spot_4h_snapshot)
            except (StaleMarketData, ValueError):
                # Context gaps block only new Spot entries; Perp and hard exits continue.
                spot_4h_snapshot = None
            utc_day = now.astimezone(timezone.utc).date()
            if last_spot_check_day != utc_day:
                limit = max(
                    legacy_spot_parameters.entry_window,
                    legacy_spot_parameters.exit_window,
                    legacy_spot_parameters.atr_period,
                ) + 2
                spot_snapshot = spot_market.snapshot(
                    "BTCUSDT", now=now, candle_limit=max(35, limit)
                )
                daily = spot_market.closed_candles()
                if daily:
                    closed_at = datetime.fromtimestamp(
                        int(daily[-1][6]) / 1000, tz=timezone.utc
                    )
                    if cached_daily_close != closed_at:
                        legacy_spot_observation = evaluate_donchian(
                            daily, legacy_spot_parameters
                        )
                        cached_daily_close = closed_at
                last_spot_check_day = utc_day
            selected_spot_rule = spot_4h_rule or legacy_spot_rule or legacy_spot_parameters
            selected_spot_observation = (
                spot_4h_observation if spot_4h_rule else legacy_spot_observation
            )
            selected_spot_snapshot = (
                spot_4h_snapshot if spot_4h_rule and spot_4h_snapshot
                else spot_snapshot
            )
            risk_result = run_parent_risk_cycle(
                store, snapshot, selected_spot_observation,
                spot_snapshot=selected_spot_snapshot,
                spot_rule=selected_spot_rule, perp_rule=perp_rule,
                legacy_spot_observation=legacy_spot_observation,
                now=now,
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
            if (spot_4h_rule is not None and spot_4h_snapshot is not None
                    and spot_4h_event and spot_4h_observation
                    and spot_4h_observation.entry):
                if store.claim_scheduler_run(
                    "paper_spot_4h", datetime.fromtimestamp(
                        cached_4h_close / 1000, timezone.utc
                    ), started_at=now
                ):
                    slots[DecisionScope.SPOT_4H] = (
                        "paper_spot_4h", datetime.fromtimestamp(
                            cached_4h_close / 1000, timezone.utc
                        )
                    )
            result = risk_result
            if slots:
                try:
                    result = run_parent_paper_cycle(
                        store, provider, snapshot, selected_spot_observation,
                        spot_snapshot=selected_spot_snapshot,
                        spot_rule=selected_spot_rule, perp_rule=perp_rule,
                        legacy_spot_observation=legacy_spot_observation,
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
                    rule = perp_rule
                    scoped_snapshot = snapshot
                    try:
                        record_compact_shadow(
                            store, provider, scoped_snapshot, scope=scope,
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
            result["assets"] = asset_results
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
                        for scope in (DecisionScope.SPOT_DAILY, DecisionScope.PERP_INTRADAY):
                            evaluate_compact_experiment(
                                store, scope=scope, evaluated_at=now
                            )
                            challenger_id = store.scoped_rule_registry(scope).get(
                                "challenger_id"
                            )
                            from intraday.replay_v2.automation import route_requires_v2
                            if challenger_id and not route_requires_v2(store,'BTCUSDT',scope):
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
    state = latest_parent_market_view(store, state)
    if command == "activate-paper":
        from intraday.replay_v2.automation import route_requires_v2
        if any(route_requires_v2(store,'BTCUSDT',scope) for scope in (DecisionScope.SPOT_4H,DecisionScope.PERP_INTRADAY)):
            raise SystemExit('selected BTC route requires scoped v2 execution admission; no v1 parent activation fallback')
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
            perp_snapshot = store.latest_snapshot(market="binance_usdm_perp")
            spot_snapshot = store.latest_snapshot(market="binance_spot")
            state = flatten_parent_paper_positions(
                store,
                state,
                now=now,
                spot_bid=spot_snapshot.bid if spot_snapshot else None,
                spot_ask=spot_snapshot.ask if spot_snapshot else None,
                perp_bid=perp_snapshot.bid if perp_snapshot else None,
                perp_ask=perp_snapshot.ask if perp_snapshot else None,
                actor="cli",
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


def _read_cryptorank_key(path: Path | None) -> str | None:
    if path is None:
        return None
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise PermissionError("CryptoRank API key path must be a regular file")
    if os.geteuid() != 0 and details.st_uid != os.getuid():
        raise PermissionError("CryptoRank API key file must be owned by the current user")
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise PermissionError("CryptoRank API key file permissions must be 0600")
    value = path.read_text(encoding="utf-8").strip()
    if not value or any(character in value for character in "\r\n\0"):
        raise ValueError("CryptoRank API key file is invalid")
    return value


def _external_context_loop(config: IntradayConfig) -> None:
    store = IntradayStore(config.database)
    liquidation_feed = AsterLiquidationFeed()
    liquidation_feed.start()
    collectors = {
        "aster": AsterCollector(liquidation_feed),
        "variational": VariationalCollector(),
        "lighter": LighterCollector(),
    }
    cadences = {
        "aster": config.aster_interval_seconds,
        "variational": config.variational_interval_seconds,
        "lighter": config.lighter_interval_seconds,
    }
    try:
        key = _read_cryptorank_key(config.cryptorank_api_key_file)
    except (OSError, ValueError) as error:
        print(json.dumps({"external_context": {"cryptorank": type(error).__name__}}), flush=True)
    else:
        if key is not None:
            collectors["cryptorank"] = CryptoRankCollector(key)
            cadences["cryptorank"] = config.cryptorank_interval_seconds
    last_retention_date = None
    while True:
        now = datetime.now(timezone.utc)
        if last_retention_date != now.date():
            store.prune_external_observations(before=now - timedelta(days=30))
            last_retention_date = now.date()
        results = {}
        for source, collector in collectors.items():
            slot = claim_cadence(store, f"external_{source}", now, cadences[source])
            if slot is None:
                continue
            result = run_external_context_cycle(
                store, collectors={source: collector}, now=now
            )
            failed = bool(result["failed"])
            partial = bool(result.get("partial"))
            store.finish_scheduler_run(
                f"external_{source}", slot,
                status="error" if failed or partial else "success",
                error_code=(
                    "CollectorError"
                    if failed
                    else "PartialCollectorError"
                    if partial
                    else None
                ),
                finished_at=datetime.now(timezone.utc),
            )
            results[source] = (
                "error" if failed else "partial" if partial else "recorded"
            )
        if results:
            print(json.dumps({"external_context": results}), flush=True)
        time.sleep(config.external_context_interval_seconds)


def _start_portfolio_hyperliquid(
    config: IntradayConfig,
) -> dict[str, HyperliquidFeed] | None:
    if not config.hyperliquid_enabled:
        return None
    feeds = {
        symbol: HyperliquidFeed(
            symbol=symbol,
            metadata_interval_seconds=config.hyperliquid_metadata_interval_seconds,
        )
        for symbol in ASSET_REGISTRY
    }
    for feed in feeds.values():
        feed.start()
    return feeds


def _latest_hyperliquid_frame(
    feed: HyperliquidFeed,
    *,
    now: datetime,
    shadow: bool,
    symbol: str | None = None,
):
    """Contain malformed advisory data, not database or programming failures."""
    try:
        return feed.latest_frame(now=now)
    except (HyperliquidFrameDataError, ValidationError) as error:
        if not shadow:
            raise
        print(json.dumps({
            "status": "degraded",
            "component": "hyperliquid_capture",
            "venue": "hyperliquid",
            "symbol": symbol or getattr(feed, "symbol", None),
            "at": now.isoformat(),
            "error_code": "invalid_market_frame",
            "error_type": type(error).__name__,
        }), flush=True)
        return None


def _record_portfolio_hyperliquid(
    store: IntradayStore,
    feed: HyperliquidFeed | dict[str, HyperliquidFeed] | None,
    *,
    now: datetime,
    shadow: bool = True,
) -> None:
    if feed is None:
        return
    feeds = feed.items() if isinstance(feed, dict) else ((None, feed),)
    for symbol, current in feeds:
        frame = _latest_hyperliquid_frame(
            current, now=now, shadow=shadow, symbol=symbol,
        )
        if frame is not None:
            store.record_venue_frame(frame)


def _new_asset_market_caches(config: IntradayConfig, *, store=None) -> tuple[dict, dict]:
    catalog = store.asset_catalog() if store else ASSET_REGISTRY
    symbols = tuple(symbol for symbol in catalog if symbol != "BTCUSDT")
    perp = {
        symbol: MultiCadenceMarketCache(BinanceUsdMClient(asset_catalog=catalog))
        for symbol in symbols if catalog[symbol].binance_perp_symbol
        and DecisionScope.PERP_INTRADAY in catalog[symbol].enabled_scopes
    }
    spot = {
        symbol: MultiTimeframeSpotCache(
            BinanceSpotDailyClient(asset_catalog=catalog),
            quote_interval_seconds=config.order_book_interval_seconds,
        )
        for symbol in symbols if catalog[symbol].binance_spot_symbol
        and DecisionScope.SPOT_4H in catalog[symbol].enabled_scopes
    }
    return perp, spot


def _run_registered_asset_cycles(
    store: IntradayStore,
    provider,
    *,
    perp_markets: dict,
    spot_markets: dict,
    config: IntradayConfig,
    now: datetime,
) -> dict[str, str]:
    """Collect every registered asset with per-symbol failure isolation."""
    # New registrations become visible without restarting the soak collector.
    fresh_perp, fresh_spot = _new_asset_market_caches(config, store=store)
    for existing, fresh in ((perp_markets, fresh_perp), (spot_markets, fresh_spot)):
        for symbol in list(existing):
            if symbol not in fresh:
                del existing[symbol]
        for symbol, market_cache in fresh.items():
            existing.setdefault(symbol, market_cache)
    results = {}
    for symbol in sorted(perp_markets.keys() | spot_markets.keys()):
        for scope, markets, interval in (
            (
                DecisionScope.PERP_INTRADAY,
                perp_markets,
                config.perp_decision_interval_seconds,
            ),
            (DecisionScope.SPOT_4H, spot_markets, 14_400),
        ):
            if symbol not in markets:
                continue
            job = f"asset_{scope.value}_{symbol.lower()}"
            slot = claim_cadence(store, job, now, interval)
            if slot is None:
                continue
            try:
                snapshot = markets[symbol].snapshot(symbol, now=now)
                spot_candles = None
                if scope is DecisionScope.SPOT_4H:
                    for candle_interval in ("4h", "8h", "1d"):
                        rows = markets[symbol].closed_candles(candle_interval)
                        if rows:
                            store.record_asset_candles(symbol, candle_interval, rows)
                    spot_candles = markets[symbol].closed_candles("4h")
                result = run_asset_lifecycle_observation(
                    store,
                    provider,
                    snapshot,
                    scope=scope,
                    now=now,
                    spot_rule=(
                        store.load_scoped_challenger(scope, symbol=symbol)
                        or store.load_active_scoped_rule(scope, symbol=symbol)
                    ) if scope is DecisionScope.SPOT_4H else None,
                    spot_candles=spot_candles,
                )
            except Exception as error:
                results[f"{symbol}:{scope.value}"] = f"error:{type(error).__name__}"
                store.finish_scheduler_run(
                    job,
                    slot,
                    status="error",
                    error_code=type(error).__name__,
                    finished_at=datetime.now(timezone.utc),
                )
            else:
                results[f"{symbol}:{scope.value}"] = result
                store.finish_scheduler_run(
                    job,
                    slot,
                    status="success",
                    finished_at=datetime.now(timezone.utc),
                )
    return results


def _run_asset_rule_bootstrap_tick(
    store: IntradayStore, *, now: datetime,
    spot_client: BinanceSpotDailyClient | None = None,
) -> dict[str, str]:
    """Idempotent daily baseline work; never promotes a rule or enables paper."""
    spot_client = spot_client or BinanceSpotDailyClient(asset_catalog=store.asset_catalog())
    results = {}
    for symbol, spec in store.asset_catalog().items():
        for scope in (DecisionScope.SPOT_4H, DecisionScope.PERP_INTRADAY):
            if scope not in spec.enabled_scopes:
                continue
            if not (spec.binance_spot_symbol if scope is DecisionScope.SPOT_4H else spec.binance_perp_symbol):
                continue
            from intraday.replay_v2.automation import route_requires_v2
            selected_v2 = route_requires_v2(store,symbol,scope)
            if scope is DecisionScope.PERP_INTRADAY and symbol == "BTCUSDT":
                continue  # Existing BTC Perp champion remains on its legacy lifecycle.
            if store.load_active_scoped_rule(scope, symbol=symbol):
                continue
            job = f"asset_baseline_{scope.value}_{symbol.lower()}"
            slot = claim_cadence(store, job, now, 86_400)
            if slot is None:
                continue
            key = f"{symbol}:{scope.value}"
            try:
                if selected_v2:
                    from intraday.replay_v2.preparation import prepare_route
                    results[key] = prepare_route(store,symbol,scope,now=now,spot_client=spot_client)
                    store.finish_scheduler_run(job,slot,status='success',finished_at=datetime.now(timezone.utc))
                    continue
                rules = store.list_scoped_rules(scope, symbol=symbol)
                if not rules:
                    if scope is DecisionScope.SPOT_4H:
                        candidate = bootstrap_spot_4h_rule(
                            store, symbol, now=now, client=spot_client,
                        )
                        evaluation = replay_spot_4h_rule(
                            store, candidate.rule_id, now=now,
                        )
                        status = f"replay_{evaluation.status}"
                    else:
                        candidate = bootstrap_perp_rule(store, symbol, now=now)
                        start_perp_decision_soak(store, candidate.rule_id, now=now)
                        status = "decision_soak_started"
                else:
                    candidate = store.load_scoped_rule(rules[0]["id"])
                    rule_status = rules[0]["status"]
                    if scope is DecisionScope.SPOT_4H and rule_status == "queued":
                        refresh_spot_4h_history(
                            store, symbol, now=now, client=spot_client,
                        )
                        evaluation = replay_spot_4h_rule(
                            store, candidate.rule_id, now=now,
                        )
                        status = f"replay_{evaluation.status}"
                    elif scope is DecisionScope.PERP_INTRADAY and rule_status == "queued":
                        start_perp_decision_soak(store, candidate.rule_id, now=now)
                        status = "decision_soak_started"
                    elif (scope is DecisionScope.PERP_INTRADAY
                          and rule_status == "challenger"):
                        replay = store.latest_scoped_rule_evaluation(
                            candidate.rule_id, kind="replay"
                        )
                        if replay is None or replay.status != "pass":
                            replay = replay_perp_bootstrap(
                                store, candidate.rule_id, now=now,
                            )
                        if replay.status == "pass":
                            validation = evaluate_perp_post_replay(
                                store, candidate.rule_id, now=now,
                            )
                            status = f"post_replay_{validation.status}"
                        else:
                            status = f"replay_{replay.status}"
                    else:
                        status = "awaiting_operator_or_proposal"
                results[key] = status
            except Exception as error:
                results[key] = f"error:{type(error).__name__}"
                store.finish_scheduler_run(
                    job, slot, status="error", error_code=type(error).__name__,
                    finished_at=datetime.now(timezone.utc),
                )
            else:
                store.finish_scheduler_run(
                    job, slot, status="success",
                    finished_at=datetime.now(timezone.utc),
                )
    return results


def _asset_rule_bootstrap_loop(config: IntradayConfig) -> None:
    store = IntradayStore(config.database)
    while True:
        results = _run_asset_rule_bootstrap_tick(store, now=datetime.now(timezone.utc))
        if results:
            print(json.dumps({"asset_baselines": results}), flush=True)
        try:
            client = active_llm_client(
                store, ProviderSecretStore(config.provider_secrets_file),
            )
        except RuntimeError:
            client = None
        if client is not None:
            proposals = _run_asset_auto_proposal_tick(
                store, client=client, now=datetime.now(timezone.utc),
                confidence_review_symbols=config.confidence_review_symbols if config.confidence_review_enabled else (),
            )
            if proposals:
                print(json.dumps({"asset_auto_proposals": proposals}), flush=True)
        if config.confidence_review_enabled:
            from intraday.replay_v2.research import run_weekly_confidence
            try:
                reviews = run_weekly_confidence(config, now=datetime.now(timezone.utc))
                print(json.dumps({"confidence_reviews": reviews}), flush=True)
            except Exception as error:
                print(json.dumps({"confidence_reviews": {"status":"error", "code":type(error).__name__}}), flush=True)
        time.sleep(3600)


def _gate_automation_loop(config: IntradayConfig) -> None:
    from intraday.replay_v2.automation import run_tick
    store = IntradayStore(config.database)
    while True:
        try:
            result = run_tick(store,now=datetime.now(timezone.utc))
            if result:
                print(json.dumps({'gate_automation':result},allow_nan=False),flush=True)
        except Exception as error:
            print(json.dumps({'gate_automation':{'status':'error','code':type(error).__name__}}),flush=True)
        time.sleep(60)


def _run_asset_auto_proposal_tick(
    store: IntradayStore, *, client, now: datetime, confidence_review_symbols=(),
) -> dict[str, str]:
    results = {}
    for symbol, spec in store.asset_catalog().items():
        for scope in (DecisionScope.SPOT_4H, DecisionScope.PERP_INTRADAY):
            from intraday.replay_v2.automation import route_requires_v2
            if route_requires_v2(store,symbol,scope):
                continue  # Weekly deterministic evaluation never auto-tunes/model-calls.
            if scope is DecisionScope.PERP_INTRADAY and symbol in confidence_review_symbols:
                continue
            if scope not in spec.enabled_scopes:
                continue
            job = f"asset_proposal_{scope.value}_{symbol.lower()}"
            slot = claim_cadence(store, job, now, 86_400)
            if slot is None:
                continue
            key = f"{symbol}:{scope.value}"
            try:
                candidate = auto_propose_asset_rule(
                    store, symbol, scope, client=client, now=now,
                )
                results[key] = candidate.rule_id if candidate else "not_eligible"
            except Exception as error:
                results[key] = f"error:{type(error).__name__}"
                store.finish_scheduler_run(
                    job, slot, status="error", error_code=type(error).__name__,
                    finished_at=datetime.now(timezone.utc),
                )
            else:
                store.finish_scheduler_run(
                    job, slot, status="success",
                    finished_at=datetime.now(timezone.utc),
                )
    return results


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
    if arguments.command == "execution":
        dispatch_execution(arguments)
        return
    if arguments.command == "perp":
        dispatch_perp(arguments)
        return
    if arguments.command == "connect" and arguments.connect_role in {"bnb", "hl"}:
        dispatch_connect(arguments)
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
    if arguments.command == "upgrade":
        try:
            if arguments.output is not None:
                prepare_report_output(arguments.output)
            report = preflight_upgrade_backup(
                arguments.backup, work_dir=arguments.work_dir,
            )
            if arguments.output is not None:
                write_report(report, arguments.output)
        except (FileExistsError, PermissionError, OSError, ValueError) as error:
            raise SystemExit(str(error)) from error
        sys.stdout.write(serialize_report(report))
        if report["status"] != "pass":
            raise SystemExit(1)
        return
    raw_arguments = sys.argv[1:]
    if (
        arguments.command == "backup"
        and arguments.backup_command == "create"
        and arguments.database is None
    ):
        deployment = deployment_cli.load_deployment()
        if deployment is not None:
            output_dir = prepare_backup_directory(
                arguments.output_dir or default_backup_directory()
            )
            command = deployment_cli.backup_command(
                deployment,
                output_dir,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
            )
            code = deployment_cli.execute(command, cwd=deployment.project_root)
            if code:
                raise SystemExit(code)
            return
    if (
        arguments.command == "portfolio"
        and arguments.portfolio_command == "soak"
        and arguments.soak_command == "evaluate"
        and arguments.database is None
    ):
        deployment = deployment_cli.load_deployment()
        if deployment is not None:
            code = deployment_cli.execute(
                deployment_cli.soak_evaluate_command(
                    deployment, at=arguments.at,
                ),
                cwd=deployment.project_root,
            )
            if code:
                raise SystemExit(code)
            return
    if (
        arguments.command == "portfolio"
        and arguments.portfolio_command == "soak"
        and arguments.soak_command == "report"
        and arguments.database is None
    ):
        deployment = deployment_cli.load_deployment()
        if deployment is not None:
            try:
                output = (
                    prepare_report_output(arguments.output)
                    if arguments.output is not None
                    else None
                )
                command = deployment_cli.soak_report_command(
                    deployment,
                    output=output,
                    owner_uid=os.getuid() if output is not None else None,
                    owner_gid=os.getgid() if output is not None else None,
                )
            except (FileExistsError, PermissionError, ValueError) as error:
                raise SystemExit(str(error)) from error
            code = deployment_cli.execute(command, cwd=deployment.project_root)
            if code:
                raise SystemExit(code)
            return
    if arguments.command == "positions" and arguments.database is None:
        deployment = deployment_cli.load_deployment()
        if deployment is not None:
            code = deployment_cli.execute(
                deployment_cli.positions_command(deployment),
                cwd=deployment.project_root,
            )
            if code:
                raise SystemExit(code)
            return
    if arguments.command == "assets" and arguments.database is None and getattr(arguments,"report_dir",None) is None:
        deployment = deployment_cli.load_deployment()
        if deployment is not None:
            code = deployment_cli.execute(
                deployment_cli.admin_command(deployment, raw_arguments),
                cwd=deployment.project_root,
            )
            if code:
                raise SystemExit(code)
            return
    if arguments.command == "replay":
        if arguments.database is None and arguments.report_dir is None:
            deployment = deployment_cli.load_deployment()
            if deployment is not None:
                try:
                    command = docker_replay_command(deployment, raw_arguments,
                        config_path=getattr(arguments, "config", None))
                except (OSError, ValueError) as error:
                    raise SystemExit(f"replay unavailable: {error}") from None
                code = deployment_cli.execute(command, cwd=deployment.project_root)
                if code:
                    raise SystemExit(code)
                return
        dispatch_replay(arguments)
        return
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
    if arguments.command == "backup":
        try:
            if arguments.backup_command == "create":
                result = create_backup(
                    resolve_database_path(arguments.database),
                    arguments.output_dir or default_backup_directory(),
                    owner_uid=arguments.owner_uid,
                    owner_gid=arguments.owner_gid,
                )
            else:
                result = verify_backup(arguments.backup_path)
        except (FileNotFoundError, FileExistsError, PermissionError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
        return
    if arguments.command == "positions":
        database = resolve_database_path(arguments.database)
        if not database.is_file():
            raise SystemExit(f"database does not exist: {database}")
        store = IntradayStore(database)
        print(
            json.dumps(
                build_positions_snapshot(store, now=datetime.now(timezone.utc)),
                indent=2,
            )
        )
        return
    if arguments.command == "assets":
        _assets_cli(arguments)
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
        if parent is not None:
            parent = latest_parent_market_view(store, parent)
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
                execution_database=arguments.execution_database,
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
                frame = _latest_hyperliquid_frame(
                    hyperliquid, now=now,
                    shadow=config.cross_venue_mode != "active", symbol=config.symbol,
                )
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
