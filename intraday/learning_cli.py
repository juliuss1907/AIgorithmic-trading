"""`aigt learning`: the weekly learning review and the ledger of its proposals."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from intraday import learning_review, setup2
from intraday.config import default_provider_secrets_path, resolve_database_path


def _when(text):
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError("review times must carry a timezone")
    return value.astimezone(timezone.utc)


def add_learning_parser(commands):
    parser = commands.add_parser("learning", help="weekly learning review: facts by code, reading by an LLM")
    actions = parser.add_subparsers(dest="learning_command", required=True)
    review = actions.add_parser("review", help="collect a week of facts and, with --profile, ask the LLM")
    review.add_argument("--database", default=None)
    review.add_argument("--execution-database", type=Path, default=None,
                        help="Demo execution journal; without it the review has no trades")
    review.add_argument("--end", type=_when, default=None, help="ISO time; default the last closed H4 bar")
    review.add_argument("--days", type=int, default=7)
    review.add_argument("--profile", default=None, help="llm provider profile id")
    review.add_argument("--no-llm", action="store_true", help="facts only, no model call")
    review.add_argument("--notify", action="store_true", help="queue the Telegram summary")
    review.add_argument("--output", type=Path, default=None, help="report directory")
    proposals = actions.add_parser("proposals", help="list or decide the proposals of past reviews")
    proposals.add_argument("action", choices=("list", "approve", "reject"))
    proposals.add_argument("proposal_id", nargs="?")
    proposals.add_argument("--status", choices=("pending", "approved", "rejected"), default=None)
    proposals.add_argument("--actor", default=None)
    proposals.add_argument("--note", default=None)
    proposals.add_argument("--output", type=Path, default=None, help="report directory")


def report_dir(output=None) -> Path:
    from intraday.replay_v2.artifacts import resolve_report_dir
    return Path(output).expanduser() if output else resolve_report_dir()/"learning"


def _review(arguments, now):
    from intraday.provider_profiles import ProviderSecretStore
    from intraday.soak_report import write_report
    from intraday.store import IntradayStore
    if not arguments.no_llm and not arguments.profile:
        raise ValueError("pass --profile <llm profile id> or --no-llm")
    if not 1 <= arguments.days <= 31:
        raise ValueError("--days must be between 1 and 31")
    directory = report_dir(arguments.output)
    database = resolve_database_path(arguments.database)
    end = arguments.end or setup2.floor_h4(now)
    start = end-timedelta(days=arguments.days)
    reader = IntradayStore(database, read_only=True)
    facts = learning_review.collect_week(reader, arguments.execution_database, start=start, end=end,
                                         proposals=learning_review.load_proposals(directory))
    review_id = f"lr-{end:%Y%m%d}-{now:%H%M%S}"
    review, added = None, []
    if not arguments.no_llm:
        secrets = os.getenv("INTRADAY_PROVIDER_SECRETS_FILE")
        store = IntradayStore(database)
        client = learning_review.review_client(
            store, ProviderSecretStore(Path(secrets).expanduser() if secrets else default_provider_secrets_path()),
            arguments.profile)
        review = learning_review.review_week(client, facts, now=now)
        if review["status"] == "valid":
            added = learning_review.record_proposals(directory, review_id, review["answer"], now=now)
    payload = dict(review_id=review_id, created_at=now.isoformat(), facts_hash=learning_review._hash(facts),
                   facts=facts, review=review, proposals=[p["id"] for p in added])
    path = write_report(payload, directory/f"{review_id}.json")
    summary = learning_review.telegram_summary(review_id, facts, review, added)
    if arguments.notify:
        IntradayStore(database).enqueue_notification(f"learning_review:{review_id}", "learning_review", summary,
                                                     created_at=now)
    return dict(report=str(path), status=review["status"] if review else "facts_only",
                errors=review["errors"] if review else [], proposals=[p["id"] for p in added], summary=summary)


def dispatch_learning(arguments):
    now = datetime.now(timezone.utc)
    try:
        if arguments.learning_command == "review":
            print(json.dumps(_review(arguments, now), indent=2, ensure_ascii=False))
            return
        directory = report_dir(arguments.output)
        if arguments.action == "list":
            items = [p for p in learning_review.load_proposals(directory)
                     if arguments.status is None or p["status"] == arguments.status]
            print(json.dumps(items, indent=2, ensure_ascii=False))
            return
        if not arguments.proposal_id or not arguments.actor:
            raise ValueError("approve and reject need a proposal id and --actor")
        decided = learning_review.decide_proposal(
            directory, arguments.proposal_id, status="approved" if arguments.action == "approve" else "rejected",
            actor=arguments.actor, note=arguments.note, now=now)
        print(json.dumps(decided, indent=2, ensure_ascii=False))
    except (ValueError, FileExistsError, PermissionError) as error:
        raise SystemExit(str(error)) from error
