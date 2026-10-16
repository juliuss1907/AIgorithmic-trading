"""Explicit local Jev review commands; dry-run never reads credentials or calls a provider."""

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

from intraday.jev_review import authorize_plan, build_plan, database_routes, paired_replay, route_identity, scan_route
from intraday.jev_review_cache import AnswerCache, DEFAULT_CACHE, canonical
from intraday.jev_review_budget import CollectionBudget
from intraday.jev_review_provider import ReviewProvider, collect_missing
from intraday.provider_profiles import ProviderSecretStore
from intraday.replay_v2.artifacts import publish_report
from intraday.replay_v2.contracts import utc

BUDGET_FILE = DEFAULT_CACHE.parent/'collection-costs.sqlite3'


def add_jev_review_parser(commands):
    parser = commands.add_parser('jev-review', help='offline Setup-2 branch-A research; never changes runtime')
    actions = parser.add_subparsers(dest='jev_review_command', required=True)
    for name in ('collect', 'replay'):
        command = actions.add_parser(name)
        command.add_argument('--database', type=Path, required=True, help='explicit private research database')
        command.add_argument('--funding-dir', type=Path, required=True)
        command.add_argument('--end', type=datetime.fromisoformat, required=True)
        command.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
        command.add_argument('--output', type=Path, help='new JSON receipt, refuses overwrite')
        if name == 'collect':
            mode = command.add_mutually_exclusive_group()
            mode.add_argument('--dry-run', action='store_true', help='default: estimate only, no credentials/network')
            mode.add_argument('--execute', action='store_true', help='paid collection; requires separate operator approval')
            command.add_argument('--expected-plan-hash', help='the exact dry-run plan approved by the operator')
            command.add_argument('--approved-plan', type=Path, help='original approved dry-run JSON, also used on resume')
            command.add_argument('--secrets-file', type=Path)
            command.add_argument('--profile-id')
            command.add_argument('--interval', type=float, default=1, help='minimum seconds between calls')
            command.add_argument('--retries', type=int, choices=(0, 1, 2), default=2)
            command.add_argument('--max-cost-usd', type=Decimal, default=Decimal('1'),
                                 help='cumulative cache cost limit, at most one USD including retries')
        else:
            command.add_argument('--report-dir', type=Path, required=True)


def _write_output(path, result):
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as stream:
            stream.write(canonical(result)+'\n')
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


def dispatch_jev_review(args):
    try:
        end = utc(args.end)
        cache_path = args.cache.expanduser().resolve()
        repo = Path(__file__).resolve().parents[1]
        if cache_path == args.database.expanduser().resolve() or cache_path.is_relative_to(repo):
            raise ValueError('review cache must be separate from the source database and outside the repository')
        if args.output is not None and args.output.exists():
            raise FileExistsError('review output already exists')
        # Scan without outcomes. Do not instantiate ProviderSecretStore on this path.
        signals, identities = [], []
        for config, data in database_routes(args.database, end, funding_dir=args.funding_dir):
            found = scan_route(config, data)
            signals.extend(found)
            identities.append(route_identity(config, data, found))
        with AnswerCache(cache_path) as cache:
            plan = build_plan(signals, cache, route_inputs=identities)
        if args.jev_review_command == 'collect':
            if not args.execute:
                _write_output(args.output, plan)
                return
            if args.approved_plan is None or not args.expected_plan_hash:
                raise ValueError('execute requires the approved plan receipt and its hash')
            approved = json.loads(args.approved_plan.read_text())
            authorize_plan(plan, approved, args.expected_plan_hash)
            if args.secrets_file is None or not args.profile_id:
                raise ValueError('execute requires explicit credential file and profile id')
            # No credential values or raw provider errors are printed or persisted here.
            try:
                credential = ProviderSecretStore(args.secrets_file).get(args.profile_id)
            except (ValueError, OSError, KeyError):
                raise ValueError('review provider credential unavailable; inspect the profile securely') from None
            provider = ReviewProvider(credential)
            with AnswerCache(cache_path, writable=True) as cache, AnswerCache(BUDGET_FILE, writable=True) as cost_journal:
                if cache.used_digest(s.tokens for s in signals) != plan['used_answers_sha256']:
                    raise ValueError('cache changed since the approved collection plan')
                budget = CollectionBudget(cost_journal, args.max_cost_usd)
                budget.include_previous(cache)
                collected = collect_missing((s.tokens for s in signals), cache, provider,
                                            interval=args.interval, retries=args.retries, budget=budget)
                collected['budget'] = budget.payload()
            _write_output(args.output, dict(approved_plan_hash=approved['plan_hash'],
                                           current_plan_hash=plan['plan_hash'], collection=collected))
            return
        results = []
        with AnswerCache(cache_path) as cache:
            for config, data in database_routes(args.database, end, funding_dir=args.funding_dir):
                found = [s for s in signals if s.rule_id == config.rule_id]
                pair = paired_replay(config, data, found, cache)
                saved = {name: publish_report(args.report_dir, pair[name]) for name in ('rule_only', 'reviewed')}
                results.append(dict(symbol=config.symbol, market=config.market, reports=saved,
                    result_ids={name: pair[name]['result_id'] for name in saved},
                    summaries={name: pair[name]['summary'] for name in saved},
                    counterfactual=pair['counterfactual'], overlay=pair['overlay']))
        _write_output(args.output, dict(research_only=True, results=results))
    except (ValueError, OSError, KeyError) as error:
        # Credential lookup errors may contain a profile id, never a key. Provider errors are redacted.
        raise SystemExit(str(error)) from None
