"""`aigt setup2`: bar backfill, read-only evaluation and operator-created Setup-2 candidates."""

from datetime import datetime, timezone
import json

from intraday import setup2_store
from intraday.assets import ticker_symbol
from intraday.config import resolve_database_path
from intraday.contracts import ScopedRuleCandidate, SpotRuleParameters

MARKETS = ('spot',)  # perp joins with stage C.


def add_setup2_parser(commands):
    parser = commands.add_parser("setup2", help="Setup-2 rule data and candidates (ADR-006)")
    actions = parser.add_subparsers(dest="setup2_command", required=True)
    for name in ("backfill", "evaluate", "candidate"):
        command = actions.add_parser(name)
        command.add_argument("--database", default=None)
        command.add_argument("--symbol", type=ticker_symbol, required=True)
        command.add_argument("--market", choices=MARKETS, required=True)
        if name == "candidate":
            command.add_argument("--actor", required=True)
            command.add_argument("--reason", required=True)
            command.add_argument("--retire-open", action="store_true",
                                 help="reject a queued or challenger legacy candidate first")


def candidate_rule(store, symbol, market, *, actor, reason, now, retire_open=False):
    scope = setup2_store.SCOPES[market]
    setup2_store.coin_anchor(store, symbol)  # Only coins in the active set.
    for row in store.list_scoped_rules(scope, symbol=symbol):
        if row["status"] not in {"queued", "replay_passed", "challenger"}:
            continue
        if not retire_open:
            raise ValueError("an open candidate exists; pass --retire-open to reject it first")
        if row["status"] == "queued":
            store.update_scoped_rule_status(row["id"], expected="queued", status="rejected")
        elif row["status"] == "challenger":
            store.reject_scoped_challenger(row["id"], now=now)
        else:
            raise ValueError("a replay-passed candidate must be started or rejected through its lifecycle first")
    champion = store.load_active_scoped_rule(scope, symbol=symbol)
    candidate = ScopedRuleCandidate.create(
        rule_id=f"{symbol.lower()}-{scope.value.replace('_', '-')}-setup2-v1-{int(now.timestamp())}",
        parent_rule_id=champion.rule_id if champion else "bootstrap", thesis_id="adr-006",
        symbol=symbol, scope=scope,
        parameters=SpotRuleParameters(entry_profile="setup2_v1", entry_window=30, exit_window=10),
        created_at=now, model_ref="operator/"+actor.strip(), prompt_version="setup2-v1",
        rationale=reason.strip())
    store.register_scoped_rule(candidate)
    return candidate


def dispatch_setup2(arguments):
    from intraday.store import IntradayStore
    database = resolve_database_path(arguments.database)
    now = datetime.now(timezone.utc)
    try:
        if arguments.setup2_command == "evaluate":
            store = IntradayStore(database, read_only=True)
            anchor = setup2_store.coin_anchor(store, arguments.symbol)
            observation = setup2_store.evaluate(store, arguments.symbol, arguments.market, anchor=anchor, now=now)
            print(json.dumps(observation.payload(), indent=2))
            return
        store = IntradayStore(database)
        if arguments.setup2_command == "backfill":
            anchor = setup2_store.coin_anchor(store, arguments.symbol)
            result = setup2_store.sync(store, arguments.symbol, arguments.market, anchor=anchor, now=now)
        else:
            candidate = candidate_rule(store, arguments.symbol, arguments.market, actor=arguments.actor,
                                       reason=arguments.reason, now=now, retire_open=arguments.retire_open)
            result = {"rule_id": candidate.rule_id, "parent_rule_id": candidate.parent_rule_id,
                      "parameters": candidate.parameters.model_dump(mode="json")}
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result, indent=2, default=str))
