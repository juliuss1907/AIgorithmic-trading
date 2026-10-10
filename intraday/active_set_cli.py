"""`aigt active-set`: show, initialize or switch the audited three-coin active set (ADR-004)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from intraday import active_set
from intraday.assets import ticker_symbol
from intraday.config import resolve_database_path

# ADR-005 freeze minus 600 H4 bars: live indicators then match the weekly prospective replay.
INITIAL_ANCHOR = datetime(2026, 7, 2, tzinfo=timezone.utc)


def add_active_set_parser(commands):
    parser = commands.add_parser("active-set", help="audited three-coin active set (ADR-004)")
    actions = parser.add_subparsers(dest="active_set_command", required=True)
    show = actions.add_parser("show")
    show.add_argument("--database", default=None)
    for name in ("init", "switch"):
        command = actions.add_parser(name)
        command.add_argument("--database", default=None)
        command.add_argument("--coins", help="comma-separated coins with equal weights, e.g. ETH,NEAR,SOL")
        command.add_argument("--mode", choices=("spot", "perp", "both"), default="both")
        command.add_argument("--coin", action="append", default=[], metavar="COIN:MODE:SPOT_WEIGHT:PERP_WEIGHT",
                             help="explicit per-coin mode and weights; repeat per coin")
        command.add_argument("--split", default="60/40", metavar="SPOT/PERP", help="capital split in percent")
        command.add_argument("--execution-database", type=Path,
                             default=Path("/app/state/execution/binance-demo.sqlite3"))
        command.add_argument("--no-demo-portfolio", action="store_true",
                             help="attest Demo execution was never configured when its database is absent")
        command.add_argument("--actor", required=True)
        command.add_argument("--reason", required=True)


def _anchor(previous, symbol, now):
    if previous and symbol in previous.plan.coins:
        return previous.plan.coins[symbol].indicator_anchor
    if previous is None:
        return INITIAL_ANCHOR
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return epoch + (now - epoch)//active_set.H4*active_set.H4 - active_set.WARMUP_BARS*active_set.H4


def build_plan(arguments, previous, now):
    spot, slash, perp = arguments.split.partition("/")
    if not slash:
        raise ValueError("--split requires SPOT/PERP percentages")
    splits = dict(spot_split=Decimal(spot)/100, perp_split=Decimal(perp)/100)
    if bool(arguments.coins) == bool(arguments.coin):
        raise ValueError("use either --coins for equal weights or one --coin per coin")
    if arguments.coins:
        symbols = [ticker_symbol(s) for s in arguments.coins.split(",")]
        anchors = {_anchor(previous, s, now) for s in symbols}
        if len(anchors) != 1:
            raise ValueError("equal-weight switches need one anchor; use --coin per coin")
        return active_set.equal_plan(symbols, mode=arguments.mode, anchor=anchors.pop(), **{
            k: str(v) for k, v in splits.items()})
    coins = {}
    for item in arguments.coin:
        parts = item.split(":")
        if len(parts) != 4:
            raise ValueError("--coin requires COIN:MODE:SPOT_WEIGHT:PERP_WEIGHT")
        symbol = ticker_symbol(parts[0])
        if symbol in coins:
            raise ValueError("each coin appears once")
        coins[symbol] = active_set.CoinPlan(mode=parts[1], spot_weight=Decimal(parts[2]),
                                            perp_weight=Decimal(parts[3]),
                                            indicator_anchor=_anchor(previous, symbol, now))
    return active_set.ActiveSetPlan(coins=coins, **splits)


def _execution_check(arguments):
    path = arguments.execution_database
    if not path.exists():
        if not arguments.no_demo_portfolio:
            raise ValueError("execution database not found; pass --no-demo-portfolio only if Demo was never configured")
        return lambda: None
    from intraday.execution.journal import ExecutionJournal
    from intraday.execution.multi_runtime import require_paused_and_flat
    return lambda: require_paused_and_flat(ExecutionJournal(path, read_only=True))


def dispatch_active_set(arguments):
    from intraday.store import IntradayStore
    database = resolve_database_path(arguments.database)
    try:
        if arguments.active_set_command == "show":
            store = IntradayStore(database, read_only=True)
            print(json.dumps({"current": active_set.describe(active_set.current(store)),
                              "versions": len(active_set.history(store))}, indent=2, default=str))
            return
        store = IntradayStore(database)
        now = datetime.now(timezone.utc)
        previous = active_set.current(store)
        plan = build_plan(arguments, previous, now)
        reason = arguments.reason + (" [no Demo portfolio attested]" if arguments.no_demo_portfolio else "")
        version = active_set.switch(store, plan, actor=arguments.actor, reason=reason, now=now,
                                    execution_check=_execution_check(arguments),
                                    initial=arguments.active_set_command == "init")
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(active_set.describe(version), indent=2, default=str))
