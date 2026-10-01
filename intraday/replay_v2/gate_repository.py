"""Additive v2 evidence extension; no changes to v1 evaluation payloads."""

import json
import sqlite3

from intraday.replay_v2.contracts import utc, binance_gate_profile
from intraday.replay_v2.gates import GateEvaluation, profile_fingerprint
from intraday.replay_v2.metrics import encoded, fingerprint


class GateRepository:
    def __init__(self, store):
        self.store = store

    def install(self):
        if self.store.read_only:
            raise ValueError("gate migration requires an explicit writer")
        with self.store._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS replay_gate_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO replay_gate_meta VALUES ('version','1');
                CREATE TABLE IF NOT EXISTS replay_gate_evaluations (
                    id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, kind TEXT NOT NULL,
                    campaign_id TEXT, evaluated_at TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS replay_gate_rule_idx
                    ON replay_gate_evaluations(candidate_id,kind,evaluated_at);
                CREATE TRIGGER IF NOT EXISTS replay_gate_no_update
                    BEFORE UPDATE ON replay_gate_evaluations BEGIN SELECT RAISE(ABORT,'gate evaluation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS replay_gate_no_delete
                    BEFORE DELETE ON replay_gate_evaluations BEGIN SELECT RAISE(ABORT,'gate evaluation is immutable'); END;
                CREATE TABLE IF NOT EXISTS replay_gate_campaigns (
                    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, scope TEXT NOT NULL,
                    candidate_id TEXT NOT NULL, started_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','rejected','promoted')),
                    payload_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS replay_gate_active_route
                    ON replay_gate_campaigns(symbol,scope) WHERE status='active';
                CREATE TRIGGER IF NOT EXISTS replay_gate_campaign_binding
                    BEFORE UPDATE OF id,symbol,scope,candidate_id,started_at,payload_json ON replay_gate_campaigns
                    BEGIN SELECT RAISE(ABORT,'gate campaign binding is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS replay_gate_campaign_no_delete
                    BEFORE DELETE ON replay_gate_campaigns BEGIN SELECT RAISE(ABORT,'gate campaign is immutable'); END;
            """)

    def _exists(self, connection):
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='replay_gate_meta'").fetchone() is not None

    def get(self, evaluation_id):
        with self.store._connect() as connection:
            if not self._exists(connection):
                return None
            row = connection.execute("SELECT payload_json FROM replay_gate_evaluations WHERE id=?", (evaluation_id,)).fetchone()
        return GateEvaluation.model_validate_json(row["payload_json"]) if row else None

    def latest(self, candidate_id, *, kind="replay", campaign_id=None):
        with self.store._connect() as connection:
            if not self._exists(connection):
                return None
            row = connection.execute(
                "SELECT payload_json FROM replay_gate_evaluations WHERE candidate_id=? AND kind=? "
                "AND campaign_id IS ? ORDER BY evaluated_at DESC,id DESC LIMIT 1",
                (candidate_id, kind, campaign_id)).fetchone()
        return GateEvaluation.model_validate_json(row["payload_json"]) if row else None

    def current(self, symbol, scope):
        with self.store._connect() as connection:
            if not self._exists(connection):
                return None
            row = connection.execute("SELECT payload_json,status FROM replay_gate_campaigns WHERE symbol=? AND scope=? "
                "ORDER BY started_at DESC,id DESC LIMIT 1", (symbol, scope.value)).fetchone()
        return {**json.loads(row["payload_json"]), "status":row["status"]} if row else None

    def record(self, evaluation):
        self.install()
        candidate = self.store.load_scoped_rule(evaluation.candidate_id)
        if (candidate is None or candidate.content_hash != evaluation.rule_content_hash
                or (candidate.symbol,candidate.scope,candidate.rule_id) !=
                (evaluation.symbol,evaluation.scope,evaluation.replay_config.rule_id)):
            raise ValueError("gate evaluation rule identity mismatch")
        if evaluation.evaluation_id != fingerprint(evaluation.model_dump(mode="json", exclude={"evaluation_id"}))[:32]:
            raise ValueError("gate evaluation checksum mismatch")
        if evaluation.profile_hash != profile_fingerprint(evaluation.replay_config.profile):
            raise ValueError("gate profile fingerprint mismatch")
        payload = encoded(evaluation.model_dump(mode="json"))
        with self.store._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO replay_gate_evaluations VALUES (?,?,?,?,?,?)",
                (evaluation.evaluation_id,evaluation.candidate_id,evaluation.kind,evaluation.campaign_id,
                 utc(evaluation.evaluated_at).isoformat(),payload))
            saved = connection.execute("SELECT payload_json FROM replay_gate_evaluations WHERE id=?", (evaluation.evaluation_id,)).fetchone()
            if saved["payload_json"] != payload:
                raise ValueError("gate evaluation identity collision")

    def start(self, evaluation_id, *, now):
        now = utc(now)
        evaluation = self.get(evaluation_id)
        if evaluation is None or evaluation.kind != "replay" or evaluation.status != "pass":
            raise ValueError("start-soak requires exact passing v2 replay evaluation")
        latest = self.latest(evaluation.candidate_id)
        if latest.evaluation_id != evaluation_id:
            raise ValueError("start-soak requires latest v2 replay evaluation")
        if evaluation.evaluated_at > now or evaluation.replay_config.end > now:
            raise ValueError("cannot start a future evaluation")
        if evaluation.profile_hash != profile_fingerprint(binance_gate_profile()):
            raise ValueError("approved gate profile required")
        candidate = self.store.load_scoped_rule(evaluation.candidate_id)
        if candidate.content_hash != evaluation.rule_content_hash:
            raise ValueError("rule changed since replay")
        if candidate.scope not in self.store.asset_spec(candidate.symbol).enabled_scopes:
            raise ValueError("scope is not enabled")
        campaign_id = fingerprint({"evaluation_id":evaluation_id,"started_at":now.isoformat()})[:32]
        campaign = {"campaign_id":campaign_id,"candidate_id":candidate.rule_id,
            "symbol":candidate.symbol,"scope":candidate.scope.value,"started_at":now.isoformat(),
            "replay_evaluation_id":evaluation_id,"profile_hash":evaluation.profile_hash,
            "rule_content_hash":candidate.content_hash,"status":"active"}
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM replay_gate_campaigns WHERE symbol=? AND scope=? AND status='active'",
                                  (candidate.symbol,candidate.scope.value)).fetchone():
                raise ValueError("route already has an active v2 campaign")
            registry = connection.execute("SELECT champion_id,challenger_id FROM asset_scoped_rule_registry WHERE symbol=? AND scope=?",
                                          (candidate.symbol,candidate.scope.value)).fetchone()
            champion = registry["champion_id"] if registry else None
            challenger = registry["challenger_id"] if registry else None
            if champion == candidate.rule_id or candidate.parent_rule_id != (champion or "bootstrap"):
                raise ValueError("candidate lineage mismatch")
            if challenger and challenger != candidate.rule_id:
                raise ValueError("another challenger is active")
            other = connection.execute("SELECT 1 FROM scoped_rules WHERE symbol=? AND scope=? AND id<>? "
                "AND status IN ('queued','replay_passed','challenger')", (candidate.symbol,candidate.scope.value,candidate.rule_id)).fetchone()
            if other:
                raise ValueError("another candidate is active")
            stage = connection.execute("SELECT stage FROM asset_scope_lifecycle WHERE symbol=? AND scope=?",
                                       (candidate.symbol,candidate.scope.value)).fetchone()
            if stage is None or stage["stage"] not in {"shadow","soak"}:
                raise ValueError("validation requires an enabled shadow/soak route, not active trading")
            connection.execute("INSERT INTO replay_gate_campaigns VALUES (?,?,?,?,?,'active',?)",
                (campaign_id,candidate.symbol,candidate.scope.value,candidate.rule_id,now.isoformat(),encoded(campaign)))
            connection.execute("UPDATE scoped_rules SET status='challenger' WHERE id=?", (candidate.rule_id,))
            connection.execute("INSERT INTO asset_scoped_rule_registry (symbol,scope,champion_id,challenger_id,rollback_id,updated_at) "
                "VALUES (?,?,?,?,NULL,?) ON CONFLICT(symbol,scope) DO UPDATE SET challenger_id=excluded.challenger_id",
                (candidate.symbol,candidate.scope.value,champion,candidate.rule_id,now.isoformat()))
            # Keep v1 collection anchors unchanged; v2 cutoff lives in its campaign.
            connection.execute("UPDATE asset_scope_lifecycle SET stage='soak',evaluation_id=?,updated_at=? WHERE symbol=? AND scope=?",
                (evaluation_id,now.isoformat(),candidate.symbol,candidate.scope.value))
            if candidate.symbol == "BTCUSDT":
                connection.execute("UPDATE scoped_rule_registry SET challenger_id=? WHERE scope=?",
                                   (candidate.rule_id,candidate.scope.value))
        return campaign

    def finish(self, evaluation_id, *, now, promote=False):
        now = utc(now)
        evaluation = self.get(evaluation_id)
        if evaluation is None or evaluation.kind != "soak" or evaluation.evaluated_at > now:
            raise ValueError("exact v2 soak evaluation required")
        campaign = self.current(evaluation.symbol,evaluation.scope)
        if (not campaign or campaign["status"] != "active" or campaign["campaign_id"] != evaluation.campaign_id
                or evaluation.replay_evaluation_id != campaign["replay_evaluation_id"]
                or evaluation.profile_hash != campaign["profile_hash"]):
            raise ValueError("v2 campaign binding mismatch")
        latest = self.latest(evaluation.candidate_id,kind="soak",campaign_id=evaluation.campaign_id)
        if latest.evaluation_id != evaluation_id or evaluation.status != ("pass" if promote else "reject"):
            raise ValueError("exact latest passing soak required" if promote else "latest rejecting soak required")
        candidate = self.store.load_scoped_rule(evaluation.candidate_id)
        if candidate.content_hash != campaign["rule_content_hash"]:
            raise ValueError("rule changed during validation")
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT champion_id,challenger_id FROM asset_scoped_rule_registry WHERE symbol=? AND scope=?",
                                     (evaluation.symbol,evaluation.scope.value)).fetchone()
            if row is None or row["challenger_id"] != candidate.rule_id or candidate.parent_rule_id != (row["champion_id"] or "bootstrap"):
                raise ValueError("active challenger lineage mismatch")
            changed = connection.execute("UPDATE replay_gate_campaigns SET status=? WHERE id=? AND status='active'",
                ("promoted" if promote else "rejected",evaluation.campaign_id))
            if changed.rowcount != 1:
                raise ValueError("campaign is no longer active")
            if promote and row["champion_id"]:
                connection.execute("UPDATE scoped_rules SET status='hall_of_fame' WHERE id=?", (row["champion_id"],))
            connection.execute("UPDATE scoped_rules SET status=? WHERE id=?",
                               ("champion" if promote else "rejected",candidate.rule_id))
            connection.execute("UPDATE asset_scoped_rule_registry SET champion_id=?,challenger_id=NULL,rollback_id=?,updated_at=? WHERE symbol=? AND scope=?",
                (candidate.rule_id if promote else row["champion_id"],row["champion_id"],now.isoformat(),evaluation.symbol,evaluation.scope.value))
            connection.execute("UPDATE asset_scope_lifecycle SET evaluation_id=?,updated_at=? WHERE symbol=? AND scope=?",
                (evaluation_id,now.isoformat(),evaluation.symbol,evaluation.scope.value))
            if candidate.symbol == "BTCUSDT":
                connection.execute("UPDATE scoped_rule_registry SET champion_id=?,challenger_id=NULL,rollback_id=?,updated_at=? WHERE scope=?",
                    (candidate.rule_id if promote else row["champion_id"],row["champion_id"],now.isoformat(),evaluation.scope.value))
        return self.store.scoped_rule_registry(evaluation.scope,symbol=evaluation.symbol)
