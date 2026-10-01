"""Separate research CLI; never dispatches a lifecycle or execution command."""

from datetime import datetime
import json
from pathlib import Path

from pydantic import ValidationError

from intraday.config import resolve_database_path
from intraday.replay_v2.artifacts import list_reports, publish_report, read_report, resolve_report_dir
from intraday.replay_v2.contracts import ReplayConfig
from intraday.replay_v2.data import load_dataset
from intraday.replay_v2.engine import simulate


def add_replay_parser(commands):
    replay = commands.add_parser("replay", help="offline research v2, not trading approval")
    subcommands = replay.add_subparsers(dest="replay_command", required=True)
    run = subcommands.add_parser("run")
    run.add_argument("symbol")
    run.add_argument("--market", choices=("spot", "perp"), required=True)
    run.add_argument("--rule", dest="rule_id", required=True)
    run.add_argument("--from", dest="start", required=True, help="aware ISO timestamp; inclusive")
    run.add_argument("--to", dest="end", required=True, help="aware ISO timestamp; exclusive")
    run.add_argument("--profile", choices=("bnb-demo",), default="bnb-demo")
    run.add_argument("--capital", default=None, help="USDT, default 1000, maximum 10000")
    run.add_argument("--leverage", type=int, default=None, help="Perp simulation only, default 3")
    run.add_argument("--config", type=Path, help="offline JSON capital/leverage/profile overrides")
    listing = subcommands.add_parser("list")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=20)
    show = subcommands.add_parser("show")
    show.add_argument("run_id")
    funding = subcommands.add_parser("funding", help="collect immutable public settlement evidence")
    funding_commands = funding.add_subparsers(dest="funding_command", required=True)
    fetch = funding_commands.add_parser("fetch")
    fetch.add_argument("symbol")
    fetch.add_argument("--from", dest="start", required=True)
    fetch.add_argument("--to", dest="end", required=True)
    run.add_argument("--cost-profile", choices=("legacy", "binance-regular"), default="binance-regular")
    run.add_argument("--funding-id", help="verified funding snapshot ID in the report directory")
    for command in (run, listing, show, fetch):
        command.add_argument("--database", type=Path, default=None, help="explicit native source, read-only")
        command.add_argument("--report-dir", type=Path, default=None, help="explicit native report root")


def read_config_file(path):
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != ".json" or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("replay configuration must be an existing JSON file of at most 2MB")
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or set(payload)-{"capital", "leverage", "profile"}:
        raise ValueError("replay configuration accepts only capital, leverage and profile")
    return payload


def dispatch_replay(arguments):
    try:
        root = resolve_report_dir(arguments.report_dir)
        if arguments.replay_command == "funding":
            from intraday.replay_v2.funding import fetch_funding_snapshot, save_funding_snapshot
            result = save_funding_snapshot(root, fetch_funding_snapshot(arguments.symbol,
                datetime.fromisoformat(arguments.start), datetime.fromisoformat(arguments.end)))
        elif arguments.replay_command == "list":
            result = list_reports(root, offset=arguments.offset, limit=arguments.limit)
        elif arguments.replay_command == "show":
            result = read_report(root, arguments.run_id)
        else:
            overrides = read_config_file(arguments.config) if arguments.config else {}
            if "profile" not in overrides and arguments.cost_profile == "binance-regular":
                from intraday.replay_v2.contracts import binance_gate_profile
                overrides["profile"] = binance_gate_profile()
            if arguments.funding_id:
                from intraday.replay_v2.contracts import ExecutionProfile
                from intraday.replay_v2.funding import read_funding_snapshot
                profile = overrides.get("profile", ExecutionProfile())
                if isinstance(profile, dict):
                    profile = ExecutionProfile.model_validate(profile)
                overrides["profile"] = profile.model_copy(update={
                    "funding":read_funding_snapshot(root, arguments.funding_id).history})
            if arguments.capital is not None:
                overrides["capital"] = arguments.capital
            if arguments.leverage is not None:
                overrides["leverage"] = arguments.leverage
            config = ReplayConfig(symbol=arguments.symbol, market=arguments.market, rule_id=arguments.rule_id,
                start=datetime.fromisoformat(arguments.start), end=datetime.fromisoformat(arguments.end), **overrides)
            data = load_dataset(resolve_database_path(arguments.database), config)
            report = simulate(config, data)
            result = publish_report(root, report)
            result["limitations"] = report["limitations"]
    except ValidationError as error:
        # Pydantic's default string includes input values; never echo supplied credentials.
        details = "; ".join(f"{'.'.join(map(str,e['loc']))}: {e['msg']}"
                            for e in error.errors(include_input=False, include_context=False))
        raise SystemExit(f"invalid replay configuration: {details}") from None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"replay unavailable: {error}") from None
    print(json.dumps(result, indent=2, allow_nan=False))


def docker_replay_command(deployment, raw_arguments, *, config_path=None):
    """One explicitly supplied JSON config is bound read-only, never a secret directory."""
    from intraday import deployment as deployment_cli
    if config_path is None:
        return deployment_cli.admin_command(deployment, raw_arguments)
    path = Path(config_path).expanduser().resolve()
    read_config_file(path)
    if ":" in str(path):
        raise ValueError("Docker replay config path cannot contain a colon")
    args = list(raw_arguments)
    if "--config" in args:
        args[args.index("--config")+1] = "/run/replay-config.json"
    else:
        args = ["--config=/run/replay-config.json" if item.startswith("--config=") else item for item in args]
    return deployment_cli.compose_command(deployment, "--profile", "admin", "run", "--rm", "--volume",
        f"{path}:/run/replay-config.json:ro", "admin", *args)
