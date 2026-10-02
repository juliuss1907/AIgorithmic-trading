"""Explicit native/Compose administrative commands for opt-in gate automation."""
from datetime import datetime, timezone
import json
import sqlite3

from intraday.config import resolve_database_path
from intraday.store import IntradayStore
from intraday.replay_v2.automation import MARKETS, policy, projection, set_policy
from intraday.replay_v2.selection import migrate_perp


def dispatch_automation(args):
    command, now = args.asset_rules_command, datetime.now(timezone.utc)
    readonly = (command == 'migrate-perp' and args.dry_run or
                command == 'automation' and args.automation_command == 'status')
    try:
        store = IntradayStore(resolve_database_path(args.database),read_only=readonly)
        if command == 'migrate-perp':
            if args.dry_run:
                result = migrate_perp(store,args.symbols,now=now,dry_run=True)
            else:
                from intraday.replay_v2.automation_lock import gate_lock
                with gate_lock(store.database):
                    result = migrate_perp(store,args.symbols,now=now)
        else:
            if args.automation_command != 'status':
                set_policy(store,args.market,enabled=args.automation_command == 'set',now=now,
                           interval_days=getattr(args,'interval_days',7))
            markets = list(MARKETS) if args.market == 'all' else [args.market]
            result = {'policy':{m:policy(store,m) for m in markets},'rows':[], 'activation_allowed':False}
            for symbol,spec in store.asset_catalog().items():
                for market in markets:
                    scope = MARKETS[market]
                    if scope in spec.enabled_scopes:
                        try:
                            state = projection(store,symbol,scope,now=now)
                        except ValueError:
                            state = {'status':'halted','blockers':['automation_binding_invalid'],'next_action':'investigate'}
                        result['rows'].append({'symbol':symbol,'market':market,**state})
    except (OSError,sqlite3.Error,ValueError,KeyError) as error:
        raise SystemExit(f'gate automation unavailable: {type(error).__name__}') from None
    print(json.dumps(result,indent=2,allow_nan=False))
    if result.get('status') == 'partial_failure':
        raise SystemExit(1)
