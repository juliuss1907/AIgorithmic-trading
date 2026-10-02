"""Explicit, immutable Perp collection inheritance; never promotes or activates."""

from datetime import datetime, timedelta

from pydantic import field_validator

from intraday.contracts import DecisionScope, ScopedRuleCandidate
from intraday.store import IntradayStore
from intraday.replay_v2.contracts import FrozenModel, ReplayConfig, binance_gate_profile, utc
from intraday.replay_v2.data import load_dataset
from intraday.replay_v2.gate_repository import GateRepository
from intraday.replay_v2.metrics import encoded, fingerprint, dataset_fingerprint


class CollectionBinding(FrozenModel):
    candidate_id: str
    candidate_hash: str
    champion_id: str
    champion_hash: str
    symbol: str
    accepted_at: datetime
    source_config: ReplayConfig
    dataset_checksum: str
    verified_decisions: int
    quote_count: int

    _at = field_validator("accepted_at")(utc)


def read_collection_binding(store, candidate_id):
    """Optional extension: a read never creates tables or rewrites legacy hashes."""
    with store._connect() as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='perp_gate_collections' AND type='table'").fetchone() is None:
            return None
        row = c.execute("SELECT payload_json,checksum FROM perp_gate_collections WHERE candidate_id=?",
                        (candidate_id,)).fetchone()
    if row is None:
        return None
    binding = CollectionBinding.model_validate_json(row["payload_json"])
    if fingerprint(binding.model_dump(mode="json")) != row["checksum"] or binding.candidate_id != candidate_id:
        raise ValueError("Perp collection binding checksum mismatch")
    return binding


def verify_collection_binding(reader, rule, binding, *, now):
    champion = reader.load_active_scoped_rule(rule.scope, symbol=rule.symbol)
    config = binding.source_config
    if (rule.scope != DecisionScope.PERP_INTRADAY or rule.rule_id != binding.candidate_id
            or rule.content_hash != binding.candidate_hash or rule.symbol != binding.symbol
            or champion is None or champion.rule_id != binding.champion_id
            or champion.content_hash != binding.champion_hash or rule.parent_rule_id != champion.rule_id
            or rule.parameters != champion.parameters or config.rule_id != champion.rule_id
            or config.market != "perp" or config.symbol != rule.symbol
            or binding.accepted_at > now or rule.created_at > now or config.end > binding.accepted_at):
        raise ValueError("Perp collection rule/lineage binding mismatch")
    data = load_dataset(reader.database, config, reader=reader)
    if dataset_fingerprint(data) != binding.dataset_checksum:
        raise ValueError("Perp collection evidence changed since inheritance")
    return binding


def _candidate(champion, start, now):
    identity = fingerprint({"champion_hash": champion.content_hash, "start": start.isoformat()})[:20]
    return ScopedRuleCandidate.create(rule_id=f"{champion.symbol.lower()}-perp-v2-{identity}",
        parent_rule_id=champion.rule_id, thesis_id="operator-collection-inheritance",
        symbol=champion.symbol, scope=champion.scope, parameters=champion.parameters,
        created_at=now, model_ref="operator/deterministic", prompt_version="perp-collection-v1",
        rationale="Unchanged champion parameters; explicitly audited historical Jev collection. "
                  "Ex-post replay only, not fresh validation or trading authorization.")


def inherit_perp_collection(store, symbol, *, collection_from, now):
    """Atomically register an unchanged candidate and its audited historical prefix.

    Keep a 15-minute tail outside the immutable prefix so in-flight decisions can
    complete. Replay independently checks the complete growing collection window.
    Coverage, funding and profitability are gate requirements, not waived here.
    """
    if store.read_only:
        raise ValueError("Perp inheritance requires an explicit writer")
    start, now = utc(collection_from), utc(now)
    cutoff = now - timedelta(minutes=15)
    if start >= cutoff:
        raise ValueError("collection start must precede the settled evidence cutoff")
    reader = IntradayStore(store.database, read_only=True)
    with reader.read_snapshot():
        spec = reader.asset_spec(symbol)
        scope = DecisionScope.PERP_INTRADAY
        if scope not in spec.enabled_scopes:
            raise ValueError("Perp scope is disabled")
        lifecycle = reader.asset_lifecycle(spec.symbol, scope)
        if lifecycle is None or lifecycle.stage.value not in {"shadow", "soak"}:
            raise ValueError("inheritance requires shadow/soak, not active trading")
        champion = reader.load_active_scoped_rule(scope, symbol=spec.symbol)
        if champion is None:
            raise ValueError("Perp inheritance requires an existing champion")
        rule = _candidate(champion, start, now)
        existing = read_collection_binding(reader, rule.rule_id)
        if existing:
            verify_collection_binding(reader, reader.load_scoped_rule(rule.rule_id), existing, now=now)
            return existing.model_dump(mode="json")
        if reader.has_open_scoped_rule_candidate(scope, symbol=spec.symbol):
            raise ValueError("another candidate is active")
        campaign = GateRepository(reader).current(spec.symbol, scope)
        if campaign and campaign["status"] == "active":
            raise ValueError("route already has active v2 validation")
        config = ReplayConfig(symbol=spec.symbol, market="perp", rule_id=champion.rule_id,
                              start=start, end=cutoff, profile=binance_gate_profile())
        data = load_dataset(reader.database, config, reader=reader)
        if not data.decisions or any(code.startswith("unverified_recorded_decisions:") for code in data.limitations):
            raise ValueError("historical model-backed provenance incomplete; collection was not inherited")
        if not data.quotes:
            raise ValueError("historical quote evidence unavailable; collection was not inherited")
        # An operator cannot backdate the clock before the first stored decision.
        first = min(decision.decision.created_at for decision in data.decisions)
        if first - start > timedelta(seconds=30):
            raise ValueError("collection start precedes recorded evidence by more than one cadence")
        binding = CollectionBinding(candidate_id=rule.rule_id, candidate_hash=rule.content_hash,
            champion_id=champion.rule_id, champion_hash=champion.content_hash, symbol=spec.symbol,
            accepted_at=now, source_config=config, dataset_checksum=dataset_fingerprint(data),
            verified_decisions=len(data.decisions), quote_count=len(data.quotes))
    GateRepository(store).install()
    with store._connect() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS perp_gate_collections (
                candidate_id TEXT PRIMARY KEY REFERENCES scoped_rules(id),
                payload_json TEXT NOT NULL, checksum TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS perp_collection_no_update BEFORE UPDATE ON perp_gate_collections
                BEGIN SELECT RAISE(ABORT,'collection binding is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS perp_collection_no_delete BEFORE DELETE ON perp_gate_collections
                BEGIN SELECT RAISE(ABORT,'collection binding is immutable'); END;
        """)
        c.execute("BEGIN IMMEDIATE")
        previous = c.execute("SELECT payload_json FROM perp_gate_collections WHERE candidate_id=?",
                             (rule.rule_id,)).fetchone()
        if previous:
            return CollectionBinding.model_validate_json(previous["payload_json"]).model_dump(mode="json")
        registry = c.execute("SELECT champion_id,challenger_id FROM asset_scoped_rule_registry WHERE symbol=? AND scope=?",
                             (spec.symbol, scope.value)).fetchone()
        current = c.execute("SELECT payload_json FROM scoped_rules WHERE id=?", (champion.rule_id,)).fetchone()
        if (registry is None or registry["champion_id"] != champion.rule_id or registry["challenger_id"]
                or current is None or ScopedRuleCandidate.model_validate_json(current["payload_json"]) != champion):
            raise ValueError("champion changed during inheritance")
        if c.execute("SELECT 1 FROM replay_gate_campaigns WHERE symbol=? AND scope=? AND status='active'",
                     (spec.symbol, scope.value)).fetchone():
            raise ValueError("route already has active v2 validation")
        if c.execute("SELECT 1 FROM scoped_rules WHERE symbol=? AND scope=? AND status IN ('queued','replay_passed','challenger')",
                     (spec.symbol, scope.value)).fetchone():
            raise ValueError("another candidate is active")
        stage = c.execute("SELECT stage FROM asset_scope_lifecycle WHERE symbol=? AND scope=?",
                          (spec.symbol, scope.value)).fetchone()
        if stage is None or stage["stage"] not in {"shadow", "soak"}:
            raise ValueError("route changed during inheritance")
        c.execute("INSERT INTO scoped_rules(id,scope,symbol,parent_id,status,payload_json,created_at) VALUES (?,?,?,?,'queued',?,?)",
                  (rule.rule_id,scope.value,spec.symbol,champion.rule_id,rule.model_dump_json(),now.isoformat()))
        payload = binding.model_dump(mode="json")
        c.execute("INSERT INTO perp_gate_collections VALUES (?,?,?)", (rule.rule_id,encoded(payload),fingerprint(payload)))
    return binding.model_dump(mode="json")
