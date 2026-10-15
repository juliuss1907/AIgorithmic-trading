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
                             help="explicit per-coin mode and weights; repeat per coin (the slot profile takes COIN:MODE)")
        command.add_argument("--split", default=None, metavar="SPOT/PERP",
                             help="capital split in percent (default 60/40; the slot profile is 50/50)")
        command.add_argument("--profile", choices=("setup2_v1", "setup2_slots_v1"), default="setup2_v1",
                             help="setup2_slots_v1 is the ADR-007 slot design (up to five coins, 50/50)")
        command.add_argument("--warmup", action="append", default=[], metavar="COIN=BARS",
                             help="per-coin H4 warm-up bars, 400 to 600 (slot profile; default 600)")
        command.add_argument("--spot-proxy", action="append", default=[], metavar="COIN=H4_BOUNDARY",
                             help="Spot H4 bars before this UTC H4 boundary take their price from the coin's Perp "
                                  "series (volume zero, no entries there); the coin's first own Spot bar is typical")
        command.add_argument("--waive-min-trades", action="append", default=[], metavar="COIN:spot",
                             help="waive the replay gate's six-closed-trades criterion for a Perp-proxied Spot route")
        command.add_argument("--waive-net-return", action="append", default=[], metavar="COIN:MARKET",
                             help="operator exception: waive the replay gate's net-profit criterion for one route "
                                  "(down to a -5%% floor; trades, drawdown and coverage still apply)")
        command.add_argument("--anchor", action="append", default=[], metavar="[COIN=]YYYY-MM-DD",
                             help="indicator anchor for new coins (UTC date at an H4 boundary); `COIN=DATE` sets one "
                                  "coin, a bare date the rest. An earlier anchor gives the replay gate more history")
        command.add_argument("--watch", action="append", default=[], metavar="COIN",
                             help="rotation candidate: rule-only Setup-2 soak, no Jev and no trading")
        command.add_argument("--execution-database", type=Path,
                             default=Path("/app/state/execution/binance-demo.sqlite3"))
        command.add_argument("--no-demo-portfolio", action="store_true",
                             help="attest Demo execution was never configured when its database is absent")
        command.add_argument("--actor", required=True)
        command.add_argument("--reason", required=True)


def _anchor(previous, symbol, now, override=None):
    if previous and previous.plan.anchor(symbol):
        return previous.plan.anchor(symbol)
    if override and symbol in override:
        return override[symbol]
    if override and None in override:
        return override[None]
    if previous is None:
        return INITIAL_ANCHOR
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return epoch + (now - epoch)//active_set.H4*active_set.H4 - active_set.WARMUP_BARS*active_set.H4


def _override(arguments):
    """Anchors given on the command line: {coin or None: datetime}; None is the default for the other coins."""
    values = getattr(arguments, "anchor", None) or []
    if isinstance(values, str):
        values = [values]
    result = {}
    for item in values:
        coin, separator, date = item.rpartition("=")
        key = ticker_symbol(coin) if separator else None
        anchor = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
        if (anchor - datetime(1970, 1, 1, tzinfo=timezone.utc)) % active_set.H4:
            raise ValueError("--anchor must be an H4 boundary")
        if key in result:
            raise ValueError("one --anchor per coin and one default")
        result[key] = anchor
    return result


def _per_coin(items, parse):
    result = {}
    for item in items:
        coin, separator, value = item.partition("=")
        if not separator or ticker_symbol(coin) in result:
            raise ValueError("per-coin settings are COIN=VALUE, once per coin")
        result[ticker_symbol(coin)] = parse(value)
    return result


def _boundary(value):
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def _per_coin_settings(arguments):
    warmup = _per_coin(getattr(arguments, "warmup", []), int)
    proxy = _per_coin(getattr(arguments, "spot_proxy", []), _boundary)
    waive = {}
    for item in getattr(arguments, "waive_min_trades", []):
        coin, separator, market = item.partition(":")
        if not separator or market != "spot":
            raise ValueError("--waive-min-trades takes COIN:spot")
        waive[ticker_symbol(coin)] = True
    waive_net = {}
    for item in getattr(arguments, "waive_net_return", []):
        coin, separator, market = item.partition(":")
        if not separator or market not in ("spot", "perp"):
            raise ValueError("--waive-net-return takes COIN:spot or COIN:perp")
        waive_net.setdefault(ticker_symbol(coin), set()).add(market)
    return warmup, proxy, waive, waive_net


def build_plan(arguments, previous, now):
    profile = getattr(arguments, "profile", "setup2_v1")
    warmup, proxy, waive, waive_net = _per_coin_settings(arguments)
    if (warmup or proxy or waive or waive_net) and profile != "setup2_slots_v1":
        raise ValueError("per-coin warm-up, proxy and waiver settings belong to the slot profile")
    default_split = "50/50" if profile == "setup2_slots_v1" else "60/40"
    spot, slash, perp = (arguments.split or default_split).partition("/")
    if not slash:
        raise ValueError("--split requires SPOT/PERP percentages")
    splits = dict(spot_split=Decimal(spot)/100, perp_split=Decimal(perp)/100)
    override = _override(arguments)
    watch = {ticker_symbol(s): _anchor(previous, ticker_symbol(s), now, override)
             for s in getattr(arguments, "watch", [])}
    if bool(arguments.coins) == bool(arguments.coin):
        raise ValueError("use either --coins for equal weights or one --coin per coin")
    if arguments.coins:
        symbols = [ticker_symbol(s) for s in arguments.coins.split(",")]
        if profile == "setup2_slots_v1":
            modes = {s: arguments.mode for s in symbols}
            return active_set.slots_plan(modes, anchors={s: _anchor(previous, s, now, override) for s in symbols},
                                         watch=watch, warmup=warmup, proxy=proxy, waive=waive,
                                         waive_net=waive_net)
        anchors = {_anchor(previous, s, now, override) for s in symbols}
        if len(anchors) != 1:
            raise ValueError("equal-weight switches need one anchor; use --coin per coin")
        plan = active_set.equal_plan(symbols, mode=arguments.mode, anchor=anchors.pop(), **{
            k: str(v) for k, v in splits.items()})
        return active_set.ActiveSetPlan.model_validate({**plan.model_dump(), "watch": watch})
    if profile == "setup2_slots_v1":
        modes = {}
        for item in arguments.coin:
            symbol, colon, mode = item.partition(":")
            if not colon or mode not in ("spot", "perp", "both") or ticker_symbol(symbol) in modes:
                raise ValueError("the slot profile takes one --coin COIN:MODE per coin (spot, perp or both)")
            modes[ticker_symbol(symbol)] = mode
        return active_set.slots_plan(modes, anchors={s: _anchor(previous, s, now, override) for s in modes},
                                     watch=watch, warmup=warmup, proxy=proxy, waive=waive,
                                         waive_net=waive_net)
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
                                            indicator_anchor=_anchor(previous, symbol, now, override))
    return active_set.ActiveSetPlan(coins=coins, watch=watch, rule_profile=profile, **splits)


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
