"""`aigt setup2`: bar backfill, read-only evaluation and operator-created Setup-2 candidates."""

from datetime import datetime, timezone
import json

from intraday import setup2_store
from intraday.assets import ticker_symbol
from intraday.config import resolve_database_path
from intraday.contracts import (PerpRuleParameters, Regime, SETUP2_DONCHIAN, SETUP2_PROFILES, ScopedRuleCandidate,
                                SpotRuleParameters)

MARKETS = ('spot', 'perp')


def add_setup2_parser(commands):
    parser = commands.add_parser("setup2", help="Setup-2 rule data and candidates (ADR-006)")
    actions = parser.add_subparsers(dest="setup2_command", required=True)
    for name in ("backfill", "evaluate", "candidate"):
        command = actions.add_parser(name)
        command.add_argument("--database", default=None)
        command.add_argument("--symbol", type=ticker_symbol, required=True)
        command.add_argument("--market", choices=MARKETS, required=True)
        if name == "evaluate":
            command.add_argument("--profile", choices=SETUP2_PROFILES, default="setup2_v1")
        if name == "candidate":
            command.add_argument("--actor", required=True)
            command.add_argument("--reason", required=True)
            command.add_argument("--retire-open", action="store_true",
                                 help="reject a queued or challenger legacy candidate first")
            command.add_argument("--profile", choices=SETUP2_PROFILES, default="setup2_v1",
                                 help="setup2_v1 (30/10, ADR-006) or setup2_slots_v1 (25/10, ADR-007)")


def candidate_rule(store, symbol, market, *, actor, reason, now, retire_open=False, profile="setup2_v1"):
    if profile not in SETUP2_DONCHIAN:
        raise ValueError("unknown Setup-2 profile")
    entry_window, exit_window, _ = SETUP2_DONCHIAN[profile]
    tag = profile.replace("_", "-")
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
        rule_id=(f"{symbol.lower()}-{scope.value.replace('_', '-')}-setup2-v1-{int(now.timestamp())}"
                 if profile == "setup2_v1" else
                 f"{symbol.lower()}-{scope.value.replace('_', '-')}-{tag}-{int(now.timestamp())}"),
        parent_rule_id=champion.rule_id if champion else "bootstrap", thesis_id="adr-006" if profile == "setup2_v1" else "adr-007",
        symbol=symbol, scope=scope,
        parameters=(SpotRuleParameters(entry_profile=profile, entry_window=entry_window, exit_window=exit_window)
                    if market == "spot" else
                    PerpRuleParameters(entry_profile=profile,
                                       allowed_regimes=(Regime.TRENDING_DOWN, Regime.SIDEWAYS))),
        created_at=now, model_ref="operator/"+actor.strip(),
        prompt_version="setup2-v1" if profile == "setup2_v1" else "setup2-slots-v1",
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
            observation = setup2_store.evaluate(store, arguments.symbol, arguments.market, anchor=anchor, now=now,
                                                profile=arguments.profile)
            print(json.dumps(observation.payload(), indent=2))
            return
        store = IntradayStore(database)
        if arguments.setup2_command == "backfill":
            anchor = setup2_store.coin_anchor(store, arguments.symbol)
            result = setup2_store.sync(store, arguments.symbol, arguments.market, anchor=anchor, now=now)
        else:
            candidate = candidate_rule(store, arguments.symbol, arguments.market, actor=arguments.actor,
                                       reason=arguments.reason, now=now, retire_open=arguments.retire_open,
                                       profile=arguments.profile)
            result = {"rule_id": candidate.rule_id, "parent_rule_id": candidate.parent_rule_id,
                      "parameters": candidate.parameters.model_dump(mode="json")}
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result, indent=2, default=str))
