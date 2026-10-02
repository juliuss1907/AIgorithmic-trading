"""Snapshot-backed batch orchestration and opt-in weekly confidence review."""

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import uuid

from intraday.assets import ticker_symbol
from intraday.llm_pipeline import active_llm_client
from intraday.provider_profiles import ProviderSecretStore
from intraday.replay_v2.artifacts import _write, resolve_report_dir
from intraday.replay_v2.confidence import review_confidence
from intraday.replay_v2.confidence_reviews import ReviewStore, due_weekly, weekly_slot
from intraday.replay_v2.metrics import encoded
from intraday.replay_v2.study import STUDY_COINS, snapshot_source, study_spot
from intraday.store import IntradayStore


def file_checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()


def provider_factory(database, secrets_file):
    # Provider calls are journaled to the isolated copy, never the live source.
    def create():
        return active_llm_client(IntradayStore(database), ProviderSecretStore(secrets_file))
    return create


def run_research(database, coins, root=None, *, now=None, markets=("spot","perp"),
                 collect=True, secrets_file=None, client_factory=None):
    now = now or datetime.now(timezone.utc)
    root = resolve_report_dir(root)
    symbols = tuple(dict.fromkeys(ticker_symbol(coin) for coin in coins))
    if not symbols or not set(markets) <= {"spot","perp"}:
        raise ValueError("research requires symbols and valid markets")
    # Resolve all requested symbols/scopes before writing any research artifacts.
    source = IntradayStore(database, read_only=True)
    for symbol in symbols:
        source.asset_spec(symbol)
    identity = uuid.uuid4().hex
    directory = root/"studies"/identity
    snapshot = snapshot_source(database, directory, now=now)
    working = snapshot["research_database"]
    if client_factory is None and secrets_file is not None:
        client_factory = provider_factory(working, secrets_file)
    results = []
    for symbol in symbols:
        for market in markets:
            try:
                if market == "spot":
                    item = study_spot(working, symbol, root, now=now, collect=collect)
                else:
                    item = review_confidence(working, symbol, root, now=now,
                        client_factory=client_factory, collect_funding=collect)
                    item = {**item, "market":"perp"}
                results.append(item)
            except Exception as error:
                results.append({"symbol":symbol,"market":market,"status":"deferred",
                                "blockers":["research_failed:"+type(error).__name__],
                                "research_only":True,"activation_allowed":False})
    manifest = {"study_id":identity,"created_at":now.isoformat(),"research_only":True,
        "activation_allowed":False,"snapshot":snapshot,"research_checksum":file_checksum(working),
        "results":results,"methodology":"Independent1000USDT accounts, no automatic lifecycle writes"}
    _write(directory/"manifest.json", encoded(manifest))
    return manifest


def run_weekly_confidence(config, *, now, root=None, runner=run_research):
    if not config.confidence_review_enabled or not due_weekly(now):
        return {"status":"disabled" if not config.confidence_review_enabled else "not_due"}
    root = resolve_report_dir(root)
    reviews = ReviewStore(root)
    requested = []
    for symbol in config.confidence_review_symbols:
        previous = reviews.latest(symbol)
        if previous and (previous["week"] == weekly_slot(now) or previous["status"] == "pending_review"):
            continue
        requested.append(symbol)
    if not requested:
        return {"status":"already_reviewed"}
    return runner(config.database, requested, root, now=now, markets=("perp",),
                  secrets_file=config.provider_secrets_file)
