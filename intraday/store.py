"""SQLite audit store for the standalone intraday system."""

from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intraday.contracts import (
    AnalystReport,
    DecisionScope,
    FeatureSnapshot,
    GateDecision,
    JevDecision,
    MarketThesis,
    MarketThesisBundle,
    NewsEvent,
    NewsIngestResult,
    PaperFill,
    ModelCallRecord,
    ProviderProfile,
    ProviderRole,
    PromotionEvaluation,
    RuleCandidate,
    RuleReplayEvaluation,
    ScopedRuleCandidate,
    VenueMarketFrame,
)
from intraday.cross_venue_evaluation import CrossVenuePromotionEvaluation
from intraday.portfolio_coordinator import ParentPortfolioState
from intraday.portfolio_soak import PortfolioSoakEvaluation
from intraday.parent_paper import ParentPaperFill


def _json(model) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class IntradayStore:
    def __init__(self, database: str | Path):
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    event_time TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_event_time
                    ON snapshots(event_time, id);
                CREATE TABLE IF NOT EXISTS scheduler_runs (
                    job_name TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'error')),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_code TEXT,
                    PRIMARY KEY (job_name, scheduled_for)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    tick_id TEXT NOT NULL UNIQUE,
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
                    direction TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gates (
                    id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL UNIQUE REFERENCES decisions(id),
                    outcome TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fills (
                    id TEXT PRIMARY KEY,
                    gate_id TEXT NOT NULL UNIQUE REFERENCES gates(id),
                    filled_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'rejected')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'queued', 'replay_passed', 'challenger', 'champion',
                        'hall_of_fame', 'rejected'
                    )),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rule_registry (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    champion_id TEXT REFERENCES rules(id),
                    challenger_id TEXT REFERENCES rules(id),
                    rollback_id TEXT REFERENCES rules(id),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS news_events (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_clusters (
                    id TEXT PRIMARY KEY,
                    verified INTEGER NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS venue_market_frames (
                    id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_venue_frames_time
                    ON venue_market_frames(venue, symbol, event_time);
                CREATE TABLE IF NOT EXISTS cross_venue_evaluations (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('deferred', 'reject', 'promote')),
                    evaluated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_profiles (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK (role IN ('jev', 'llm')),
                    kind TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    last_test_status TEXT CHECK (
                        last_test_status IS NULL OR last_test_status IN ('ok', 'error')
                    ),
                    last_test_at TEXT,
                    latency_ms INTEGER,
                    error_code TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_assignments (
                    role TEXT PRIMARY KEY CHECK (role IN ('jev', 'llm')),
                    profile_id TEXT NOT NULL REFERENCES provider_profiles(id),
                    profile_fingerprint TEXT NOT NULL,
                    activated_at TEXT NOT NULL,
                    actor TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_calls (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
                    cost_usd REAL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_calls_started
                    ON model_calls(started_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS analyst_reports (
                    id TEXT PRIMARY KEY,
                    analyst TEXT NOT NULL CHECK (analyst IN ('market', 'news', 'sentiment')),
                    generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_analyst_reports_latest
                    ON analyst_reports(analyst, generated_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS market_theses (
                    id TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_theses_latest
                    ON market_theses(generated_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS market_thesis_bundles (
                    id TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_thesis_bundles_latest
                    ON market_thesis_bundles(generated_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS scoped_rules (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL CHECK (scope IN ('spot_daily', 'perp_intraday')),
                    parent_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'queued', 'replay_passed', 'challenger', 'champion',
                        'hall_of_fame', 'rejected'
                    )),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scoped_rules_status
                    ON scoped_rules(scope, status, created_at, id);
                CREATE TABLE IF NOT EXISTS scoped_rule_registry (
                    scope TEXT PRIMARY KEY CHECK (scope IN ('spot_daily', 'perp_intraday')),
                    champion_id TEXT REFERENCES scoped_rules(id),
                    challenger_id TEXT REFERENCES scoped_rules(id),
                    rollback_id TEXT REFERENCES scoped_rules(id),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS parent_portfolio_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS parent_portfolio_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_soak_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL CHECK (scope IN ('spot_daily', 'perp_intraday')),
                    status TEXT NOT NULL CHECK (status IN (
                        'success', 'skipped_no_setup', 'provider_error', 'gate_error'
                    )),
                    hard_risk_violation INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_portfolio_soak_ticks_time
                    ON portfolio_soak_ticks(created_at, scope);
                CREATE TABLE IF NOT EXISTS portfolio_soak_evaluations (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('deferred', 'reject', 'pass')),
                    evaluated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS parent_paper_fills (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL CHECK (scope IN ('spot_daily', 'perp_intraday')),
                    filled_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('spot_daily', 'perp_intraday')),
                    state_snapshot TEXT NOT NULL,
                    raw_signals TEXT NOT NULL,
                    jev_answers TEXT NOT NULL,
                    gate_passed INTEGER NOT NULL CHECK (gate_passed IN (0, 1)),
                    gate_reason TEXT,
                    rules_version TEXT NOT NULL,
                    llm_thesis TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_signals_time_scope
                    ON signals(timestamp, symbol, scope);
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_key TEXT NOT NULL UNIQUE,
                    signal_id INTEGER NOT NULL REFERENCES signals(id),
                    scope TEXT NOT NULL CHECK (scope IN ('spot_daily', 'perp_intraday')),
                    entry_fill_id TEXT NOT NULL,
                    exit_fill_id TEXT NOT NULL,
                    timestamp_open TEXT NOT NULL,
                    timestamp_close TEXT,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    position_size REAL NOT NULL,
                    pnl_abs REAL,
                    pnl_pct REAL,
                    close_reason TEXT,
                    duration_sec INTEGER,
                    is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper IN (0, 1))
                );
                CREATE INDEX IF NOT EXISTS idx_trades_signal
                    ON trades(signal_id, timestamp_close);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_entry_fill
                    ON trades(entry_fill_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_exit_fill
                    ON trades(exit_fill_id);
                CREATE TABLE IF NOT EXISTS open_trade_context (
                    scope TEXT PRIMARY KEY CHECK (scope IN ('spot_daily', 'perp_intraday')),
                    trade_key TEXT NOT NULL UNIQUE,
                    signal_id INTEGER NOT NULL REFERENCES signals(id),
                    entry_fill_id TEXT NOT NULL,
                    timestamp_open TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    position_size REAL NOT NULL,
                    entry_fee REAL NOT NULL,
                    stop_loss REAL,
                    take_profit REAL
                );
                CREATE TRIGGER IF NOT EXISTS signals_append_only_update
                BEFORE UPDATE ON signals BEGIN
                    SELECT RAISE(ABORT, 'signals are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS signals_append_only_delete
                BEFORE DELETE ON signals BEGIN
                    SELECT RAISE(ABORT, 'signals are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS trades_append_only_update
                BEFORE UPDATE ON trades BEGIN
                    SELECT RAISE(ABORT, 'trades are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS trades_append_only_delete
                BEFORE DELETE ON trades BEGIN
                    SELECT RAISE(ABORT, 'trades are append-only');
                END;
                CREATE TABLE IF NOT EXISTS rule_evaluations (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES rules(id),
                    kind TEXT NOT NULL CHECK (kind IN ('replay', 'promotion')),
                    status TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rule_evaluations_candidate
                    ON rule_evaluations(candidate_id, evaluated_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO schema_meta VALUES ('schema_version', '1');
                """
            )
            command_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(commands)")
            }
            for name, definition in {
                "payload_json": "TEXT",
                "result_json": "TEXT",
                "error_code": "TEXT",
                "applied_at": "TEXT",
            }.items():
                if name not in command_columns:
                    connection.execute(f"ALTER TABLE commands ADD COLUMN {name} {definition}")
            connection.execute(
                "UPDATE schema_meta SET value='10' WHERE key='schema_version'"
            )

    def claim_scheduler_run(
        self, job_name: str, scheduled_for: datetime, *, started_at: datetime | None = None
    ) -> bool:
        if not job_name:
            raise ValueError("scheduler job name is required")
        if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
            raise ValueError("scheduler slot must be timezone-aware")
        started_at = started_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO scheduler_runs "
                "(job_name, scheduled_for, status, started_at) VALUES (?, ?, 'running', ?)",
                (
                    job_name,
                    scheduled_for.astimezone(timezone.utc).isoformat(),
                    started_at.astimezone(timezone.utc).isoformat(),
                ),
            )
            return cursor.rowcount == 1

    def finish_scheduler_run(
        self,
        job_name: str,
        scheduled_for: datetime,
        *,
        status: str,
        error_code: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        if status not in {"success", "error"}:
            raise ValueError("scheduler status must be success or error")
        if status == "success" and error_code is not None:
            raise ValueError("successful scheduler run cannot have an error code")
        finished_at = finished_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE scheduler_runs SET status=?, finished_at=?, error_code=? "
                "WHERE job_name=? AND scheduled_for=? AND status='running'",
                (
                    status,
                    finished_at.astimezone(timezone.utc).isoformat(),
                    error_code,
                    job_name,
                    scheduled_for.astimezone(timezone.utc).isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("running scheduler slot not found")

    def scheduler_run(self, job_name: str, scheduled_for: datetime) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduler_runs WHERE job_name=? AND scheduled_for=?",
                (
                    job_name,
                    scheduled_for.astimezone(timezone.utc).isoformat(),
                ),
            ).fetchone()
        return None if row is None else dict(row)

    def record_tick(
        self,
        snapshot: FeatureSnapshot,
        decision: JevDecision,
        gate: GateDecision,
        fill: PaperFill | None,
        portfolio_state: dict | None = None,
        runtime_state: dict | None = None,
    ) -> dict:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id, tick_id FROM decisions WHERE tick_id = ?", (decision.tick_id,)
            ).fetchone()
            if existing:
                return dict(existing)
            connection.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?)",
                (snapshot.snapshot_id, snapshot.event_time.isoformat(), _json(snapshot)),
            )
            connection.execute(
                "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.tick_id,
                    snapshot.snapshot_id,
                    decision.direction.value,
                    decision.created_at.isoformat(),
                    _json(decision),
                ),
            )
            connection.execute(
                "INSERT INTO gates VALUES (?, ?, ?, ?, ?)",
                (
                    gate.gate_id,
                    gate.decision_id,
                    gate.outcome,
                    gate.evaluated_at.isoformat(),
                    _json(gate),
                ),
            )
            if fill is not None:
                connection.execute(
                    "INSERT INTO fills VALUES (?, ?, ?, ?)",
                    (fill.fill_id, fill.gate_id, fill.filled_at.isoformat(), _json(fill)),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO notification_outbox "
                    "(delivery_key, kind, message, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                    (
                        f"paper_fill:{fill.fill_id}",
                        "paper_fill",
                        (
                            f"PAPER FILL | {fill.side.upper()} {fill.quantity:.8f} BTC "
                            f"@ {fill.price:.2f} | fee {fill.fee:.4f} USDT"
                        ),
                        fill.filled_at.isoformat(),
                    ),
                )
            if portfolio_state is not None:
                connection.execute(
                    "INSERT INTO portfolio_state VALUES (1, ?, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET "
                    "payload_json = excluded.payload_json, updated_at = excluded.updated_at",
                    (
                        json.dumps(portfolio_state, sort_keys=True, separators=(",", ":")),
                        gate.evaluated_at.isoformat(),
                    ),
                )
            if runtime_state is not None:
                self._save_runtime_state(connection, runtime_state, gate.evaluated_at)
        return {"id": decision.decision_id, "tick_id": decision.tick_id}

    def record_snapshot(self, snapshot: FeatureSnapshot) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?)",
                (snapshot.snapshot_id, snapshot.event_time.isoformat(), _json(snapshot)),
            )

    def get_tick(self, tick_id: str):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT d.payload_json AS decision_json, g.payload_json AS gate_json, "
                "f.payload_json AS fill_json FROM decisions d "
                "JOIN gates g ON g.decision_id = d.id "
                "LEFT JOIN fills f ON f.gate_id = g.id WHERE d.tick_id = ?",
                (tick_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "decision": JevDecision.model_validate_json(row["decision_json"]),
            "gate": GateDecision.model_validate_json(row["gate_json"]),
            "fill": (
                PaperFill.model_validate_json(row["fill_json"])
                if row["fill_json"] is not None else None
            ),
        }

    def load_portfolio_state(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM portfolio_state WHERE singleton = 1"
            ).fetchone()
        return None if row is None else json.loads(row["payload_json"])

    def save_parent_portfolio_state(
        self,
        state: ParentPortfolioState,
        *,
        event_kind: str,
        actor: str,
    ) -> None:
        payload = _json(state)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO parent_portfolio_state VALUES (1, ?, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET "
                "payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (payload, state.updated_at.isoformat()),
            )
            connection.execute(
                "INSERT INTO parent_portfolio_events "
                "(kind, actor, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (event_kind, actor, payload, state.updated_at.isoformat()),
            )

    def load_parent_portfolio_state(self) -> ParentPortfolioState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM parent_portfolio_state WHERE singleton=1"
            ).fetchone()
        return (
            None
            if row is None
            else ParentPortfolioState.model_validate_json(row["payload_json"])
        )

    def list_parent_portfolio_events(self, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, kind, actor, payload_json, created_at "
                "FROM parent_portfolio_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_portfolio_soak_tick(
        self,
        *,
        scope: DecisionScope,
        status: str,
        created_at: datetime,
        hard_risk_violation: bool = False,
    ) -> None:
        if status not in {
            "success", "skipped_no_setup", "provider_error", "gate_error"
        }:
            raise ValueError("invalid portfolio soak status")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO portfolio_soak_ticks "
                "(scope, status, hard_risk_violation, created_at) VALUES (?, ?, ?, ?)",
                (
                    scope.value,
                    status,
                    int(hard_risk_violation),
                    created_at.isoformat(),
                ),
            )

    def list_portfolio_soak_ticks(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT scope, status, hard_risk_violation, created_at "
                "FROM portfolio_soak_ticks ORDER BY created_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_portfolio_soak_evaluation(
        self, evaluation: PortfolioSoakEvaluation
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO portfolio_soak_evaluations VALUES (?, ?, ?, ?)",
                (
                    evaluation.evaluation_id,
                    evaluation.status,
                    evaluation.evaluated_at.isoformat(),
                    _json(evaluation),
                ),
            )

    def portfolio_soak_evaluation(
        self, evaluation_id: str
    ) -> PortfolioSoakEvaluation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM portfolio_soak_evaluations WHERE id=?",
                (evaluation_id,),
            ).fetchone()
        return (
            None
            if row is None
            else PortfolioSoakEvaluation.model_validate_json(row["payload_json"])
        )

    def latest_portfolio_soak_evaluation(self) -> PortfolioSoakEvaluation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM portfolio_soak_evaluations "
                "ORDER BY evaluated_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return (
            None
            if row is None
            else PortfolioSoakEvaluation.model_validate_json(row["payload_json"])
        )

    def record_parent_paper_fill(
        self,
        fill: ParentPaperFill,
        *,
        entry_signal_id: int | None = None,
        direction: str | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        close_reason: str | None = None,
    ) -> int | None:
        if entry_signal_id is not None and fill.reduce_only:
            raise ValueError("a reduce-only fill cannot open a journal trade")
        if entry_signal_id is not None and direction is None:
            raise ValueError("an entry journal fill requires its direction")
        if close_reason is not None and not fill.reduce_only:
            raise ValueError("a journal close requires a reduce-only fill")
        if entry_signal_id is not None and close_reason is not None:
            raise ValueError("a fill cannot open and close a journal trade")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO parent_paper_fills VALUES (?, ?, ?, ?)",
                (
                    fill.fill_id,
                    fill.scope.value,
                    fill.filled_at.isoformat(),
                    _json(fill),
                ),
            )
            if entry_signal_id is not None:
                trade_key = hashlib.sha256(
                    f"{fill.scope.value}:{entry_signal_id}:{fill.fill_id}".encode()
                ).hexdigest()[:32]
                connection.execute(
                    "INSERT OR IGNORE INTO open_trade_context "
                    "(scope, trade_key, signal_id, entry_fill_id, timestamp_open, "
                    "direction, entry_price, quantity, position_size, entry_fee, "
                    "stop_loss, take_profit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fill.scope.value,
                        trade_key,
                        entry_signal_id,
                        fill.fill_id,
                        fill.filled_at.astimezone(timezone.utc).isoformat(),
                        direction,
                        fill.price,
                        fill.quantity,
                        fill.notional,
                        fill.fee,
                        stop_loss,
                        take_profit,
                    ),
                )
                context = connection.execute(
                    "SELECT * FROM open_trade_context WHERE scope=?",
                    (fill.scope.value,),
                ).fetchone()
                if context["trade_key"] != trade_key:
                    raise ValueError("scope already has a different open journal trade")
                return None
            if close_reason is None:
                return None
            existing = connection.execute(
                "SELECT id FROM trades WHERE exit_fill_id=?", (fill.fill_id,)
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            context = connection.execute(
                "SELECT * FROM open_trade_context WHERE scope=?",
                (fill.scope.value,),
            ).fetchone()
            if context is None:
                return None
            opened_at = datetime.fromisoformat(context["timestamp_open"])
            duration_sec = int((fill.filled_at - opened_at).total_seconds())
            if duration_sec < 0:
                raise ValueError("journal close precedes its entry")
            long_position = context["direction"] in {"Buy", "Strong Buy"}
            signed = 1 if long_position else -1
            gross_pnl = (
                context["quantity"] * (fill.price - context["entry_price"]) * signed
            )
            pnl_abs = gross_pnl - context["entry_fee"] - fill.fee
            pnl_pct = pnl_abs / context["position_size"] * 100
            cursor = connection.execute(
                "INSERT INTO trades "
                "(trade_key, signal_id, scope, entry_fill_id, exit_fill_id, "
                "timestamp_open, timestamp_close, direction, entry_price, exit_price, "
                "stop_loss, take_profit, position_size, pnl_abs, pnl_pct, close_reason, "
                "duration_sec, is_paper) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, 1)",
                (
                    context["trade_key"],
                    context["signal_id"],
                    fill.scope.value,
                    context["entry_fill_id"],
                    fill.fill_id,
                    context["timestamp_open"],
                    fill.filled_at.astimezone(timezone.utc).isoformat(),
                    context["direction"],
                    context["entry_price"],
                    fill.price,
                    context["stop_loss"],
                    context["take_profit"],
                    context["position_size"],
                    pnl_abs,
                    pnl_pct,
                    close_reason,
                    duration_sec,
                ),
            )
            connection.execute(
                "DELETE FROM open_trade_context WHERE scope=?", (fill.scope.value,)
            )
            return int(cursor.lastrowid)

    def record_journal_signal(
        self,
        *,
        decision_id: str,
        timestamp: datetime,
        symbol: str,
        scope: DecisionScope,
        state_snapshot: str,
        raw_signals: dict[str, float | None],
        jev_answers: dict,
        gate_passed: bool,
        gate_reason: str | None,
        rules_version: str,
        llm_thesis: str | None,
    ) -> int:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("signal timestamp must be timezone-aware")
        if not decision_id or not symbol or not state_snapshot or not rules_version:
            raise ValueError("signal identity and snapshots must be nonempty")
        if not raw_signals or any(
            isinstance(value, bool)
            or (
                value is not None
                and (
                    not isinstance(value, (int, float))
                    or not math.isfinite(value)
                )
            )
            for value in raw_signals.values()
        ):
            raise ValueError("raw_signals must contain finite numeric values")
        if not jev_answers:
            raise ValueError("jev_answers must not be empty")
        if gate_passed and gate_reason is not None:
            raise ValueError("a passed gate cannot have a rejection reason")
        if not gate_passed and not gate_reason:
            raise ValueError("a rejected gate requires a reason")
        values = (
            decision_id,
            timestamp.astimezone(timezone.utc).isoformat(),
            symbol,
            scope.value,
            state_snapshot,
            json.dumps(raw_signals, sort_keys=True, separators=(",", ":")),
            json.dumps(jev_answers, sort_keys=True, separators=(",", ":")),
            int(gate_passed),
            gate_reason,
            rules_version,
            llm_thesis,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO signals "
                "(decision_id, timestamp, symbol, scope, state_snapshot, raw_signals, "
                "jev_answers, gate_passed, gate_reason, rules_version, llm_thesis) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            row = connection.execute(
                "SELECT id, timestamp, symbol, scope, state_snapshot, raw_signals, "
                "jev_answers, gate_passed, gate_reason, rules_version, llm_thesis "
                "FROM signals WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
            if tuple(row[1:]) != values[1:]:
                raise ValueError("decision_id was reused with different journal data")
            return int(row["id"])

    def record_completed_trade(
        self,
        *,
        trade_key: str,
        signal_id: int,
        scope: DecisionScope,
        entry_fill_id: str,
        exit_fill_id: str,
        timestamp_open: datetime,
        timestamp_close: datetime,
        direction: str,
        entry_price: float,
        exit_price: float,
        stop_loss: float | None,
        take_profit: float | None,
        position_size: float,
        pnl_abs: float,
        pnl_pct: float,
        close_reason: str,
        duration_sec: int,
        is_paper: bool = True,
    ) -> int:
        if timestamp_open.tzinfo is None or timestamp_open.utcoffset() is None:
            raise ValueError("trade open timestamp must be timezone-aware")
        if timestamp_close.tzinfo is None or timestamp_close.utcoffset() is None:
            raise ValueError("trade close timestamp must be timezone-aware")
        if timestamp_close < timestamp_open or duration_sec < 0:
            raise ValueError("trade close must not precede its open")
        if min(entry_price, exit_price, position_size) <= 0:
            raise ValueError("trade prices and position size must be positive")
        values = (
            trade_key,
            signal_id,
            scope.value,
            entry_fill_id,
            exit_fill_id,
            timestamp_open.astimezone(timezone.utc).isoformat(),
            timestamp_close.astimezone(timezone.utc).isoformat(),
            direction,
            entry_price,
            exit_price,
            stop_loss,
            take_profit,
            position_size,
            pnl_abs,
            pnl_pct,
            close_reason,
            duration_sec,
            int(is_paper),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO trades "
                "(trade_key, signal_id, scope, entry_fill_id, exit_fill_id, "
                "timestamp_open, timestamp_close, direction, entry_price, exit_price, "
                "stop_loss, take_profit, position_size, pnl_abs, pnl_pct, close_reason, "
                "duration_sec, is_paper) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?)",
                values,
            )
            row = connection.execute(
                "SELECT id, trade_key, signal_id, scope, entry_fill_id, exit_fill_id, "
                "timestamp_open, timestamp_close, direction, entry_price, exit_price, "
                "stop_loss, take_profit, position_size, pnl_abs, pnl_pct, close_reason, "
                "duration_sec, is_paper FROM trades WHERE trade_key=?",
                (trade_key,),
            ).fetchone()
            if tuple(row[1:]) != values:
                raise ValueError("trade_key was reused with different journal data")
            return int(row["id"])

    def list_parent_paper_fills(self, limit: int = 100) -> list[ParentPaperFill]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM parent_paper_fills "
                "ORDER BY filled_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            ParentPaperFill.model_validate_json(row["payload_json"]) for row in rows
        ]

    @staticmethod
    def _save_runtime_state(connection, state: dict, updated_at: datetime) -> None:
        connection.execute(
            "INSERT INTO runtime_state VALUES (1, ?, ?) "
            "ON CONFLICT(singleton) DO UPDATE SET "
            "payload_json = excluded.payload_json, updated_at = excluded.updated_at",
            (
                json.dumps(state, sort_keys=True, separators=(",", ":")),
                updated_at.isoformat(),
            ),
        )

    def save_runtime_state(self, state: dict, *, updated_at: datetime) -> None:
        with self._connect() as connection:
            self._save_runtime_state(connection, state, updated_at)

    def load_runtime_state(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_state WHERE singleton = 1"
            ).fetchone()
        return None if row is None else json.loads(row["payload_json"])

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("snapshots", "decisions", "gates", "fills")
            }

    def list_decisions(self, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT d.id, d.tick_id, d.snapshot_id, d.direction, d.created_at, "
                "g.payload_json AS gate_json FROM decisions d "
                "JOIN gates g ON g.decision_id = d.id "
                "ORDER BY d.created_at DESC, d.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            gate = GateDecision.model_validate_json(item.pop("gate_json"))
            item.update({
                "gate_outcome": gate.outcome,
                "cross_venue_mode": gate.cross_venue_mode,
                "cross_venue_status": gate.cross_venue_status,
                "entry_quality_adjustment": gate.entry_quality_adjustment,
                "notional_multiplier": gate.notional_multiplier,
            })
            result.append(item)
        return result

    def list_snapshots(self, limit: int = 100_000) -> list[FeatureSnapshot]:
        if not 1 <= limit <= 2_000_000:
            raise ValueError("limit must be between 1 and 2000000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM snapshots "
                "ORDER BY event_time DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            FeatureSnapshot.model_validate_json(row["payload_json"])
            for row in reversed(rows)
        ]

    def latest_snapshot(self) -> FeatureSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM snapshots "
                "ORDER BY event_time DESC, id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else FeatureSnapshot.model_validate_json(row["payload_json"])

    def snapshot_history_bounds(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS snapshots, MIN(event_time) AS first_event_time, "
                "MAX(event_time) AS last_event_time FROM snapshots"
            ).fetchone()
        return dict(row)

    def list_snapshots_since(
        self, since: datetime, *, limit: int = 2_000_000
    ) -> list[FeatureSnapshot]:
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("snapshot boundary must be timezone-aware")
        if not 1 <= limit <= 2_000_000:
            raise ValueError("limit must be between 1 and 2000000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM snapshots "
                "WHERE julianday(event_time) >= julianday(?) "
                "ORDER BY event_time DESC, id DESC LIMIT ?",
                (since.isoformat(), limit),
            ).fetchall()
        return [
            FeatureSnapshot.model_validate_json(row["payload_json"])
            for row in reversed(rows)
        ]

    def list_recorded_decisions(
        self, limit: int = 100_000, *, since: datetime | None = None
    ) -> list[JevDecision]:
        if not 1 <= limit <= 2_000_000:
            raise ValueError("limit must be between 1 and 2000000")
        if since is not None and (since.tzinfo is None or since.utcoffset() is None):
            raise ValueError("decision boundary must be timezone-aware")
        query = "SELECT payload_json FROM decisions"
        parameters: tuple = ()
        if since is not None:
            query += " WHERE julianday(created_at) >= julianday(?)"
            parameters = (since.isoformat(),)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        parameters += (limit,)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            JevDecision.model_validate_json(row["payload_json"])
            for row in reversed(rows)
        ]

    def enqueue_command(
        self,
        command_id: str,
        kind: str,
        created_at: datetime,
        *,
        actor: str,
        payload: dict | None = None,
    ):
        allowed = {
            "pause_entries", "resume_entries", "flatten", "rollback",
            "toggle_notifications", "provider_test", "provider_activate",
            "provider_deactivate", "portfolio_pause", "portfolio_resume",
            "portfolio_flatten",
        }
        if kind not in allowed:
            raise ValueError("unsupported operator command")
        payload_json = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if payload is not None else None
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO commands "
                "(id, kind, actor, status, created_at, payload_json) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (command_id, kind, actor, created_at.isoformat(), payload_json),
            )
            row = connection.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
            if (
                row["kind"] != kind
                or row["actor"] != actor
                or row["payload_json"] != payload_json
            ):
                raise ValueError("idempotency key conflicts with an existing command")
        return dict(row)

    def list_commands(self, *, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM commands"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY created_at, id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def finish_command(
        self,
        command_id: str,
        *,
        status: str,
        result: dict | None = None,
        error_code: str | None = None,
        applied_at: datetime | None = None,
    ) -> None:
        if status not in {"applied", "rejected"}:
            raise ValueError("command status must be applied or rejected")
        if status == "applied" and error_code is not None:
            raise ValueError("applied command cannot have an error code")
        result_json = (
            json.dumps(result, sort_keys=True, separators=(",", ":"))
            if result is not None else None
        )
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE commands SET status=?, result_json=?, error_code=?, applied_at=? "
                "WHERE id=? AND status='pending'",
                (
                    status,
                    result_json,
                    error_code,
                    (applied_at or datetime.now(timezone.utc)).isoformat(),
                    command_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("pending command not found")

    def register_rule(self, rule: RuleCandidate, *, status: str = "queued") -> None:
        allowed = {"queued", "replay_passed", "challenger", "champion", "hall_of_fame", "rejected"}
        if status not in allowed:
            raise ValueError("invalid rule status")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO rules VALUES (?, ?, ?, ?, ?)",
                (
                    rule.rule_id,
                    rule.parent_rule_id,
                    status,
                    _json(rule),
                    rule.created_at.isoformat(),
                ),
            )

    def rule_status(self, rule_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM rules WHERE id=?", (rule_id,)
            ).fetchone()
        return None if row is None else row["status"]

    def load_rule(self, rule_id: str) -> RuleCandidate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM rules WHERE id=?", (rule_id,)
            ).fetchone()
        return None if row is None else RuleCandidate.model_validate_json(row["payload_json"])

    def oldest_rule(self, status: str) -> RuleCandidate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM rules WHERE status=? "
                "ORDER BY created_at, id LIMIT 1",
                (status,),
            ).fetchone()
        return None if row is None else RuleCandidate.model_validate_json(row["payload_json"])

    def has_open_rule_candidate(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM rules WHERE status IN "
                "('queued', 'replay_passed', 'challenger') LIMIT 1"
            ).fetchone()
        return row is not None

    def update_rule_status(self, rule_id: str, *, expected: str, status: str) -> None:
        transitions = {
            ("queued", "replay_passed"),
            ("queued", "rejected"),
            ("challenger", "rejected"),
        }
        if (expected, status) not in transitions:
            raise ValueError("unsupported rule status transition")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE rules SET status=? WHERE id=? AND status=?",
                (status, rule_id, expected),
            )
            if cursor.rowcount != 1:
                raise ValueError("rule is not in the expected state")

    def record_rule_replay_evaluation(self, evaluation: RuleReplayEvaluation) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO rule_evaluations VALUES (?, ?, 'replay', ?, ?, ?)",
                (
                    evaluation.evaluation_id,
                    evaluation.candidate_id,
                    evaluation.status,
                    evaluation.evaluated_at.isoformat(),
                    _json(evaluation),
                ),
            )

    def record_rule_promotion_evaluation(self, evaluation: PromotionEvaluation) -> None:
        identity = hashlib.sha256(
            f"promotion:{evaluation.candidate_id}:{evaluation.evaluated_at.isoformat()}".encode()
        ).hexdigest()[:32]
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO rule_evaluations VALUES (?, ?, 'promotion', ?, ?, ?)",
                (
                    identity,
                    evaluation.candidate_id,
                    evaluation.status,
                    evaluation.evaluated_at.isoformat(),
                    _json(evaluation),
                ),
            )

    def record_analysis(
        self,
        reports: tuple[AnalystReport, AnalystReport, AnalystReport],
        thesis: MarketThesis,
    ) -> None:
        expected = {"market", "news", "sentiment"}
        if {report.analyst for report in reports} != expected:
            raise ValueError("analysis cycle requires market, news, and sentiment reports")
        if set(thesis.source_report_ids) != {report.report_id for report in reports}:
            raise ValueError("market thesis must reference the analysis cycle reports")
        with self._connect() as connection:
            for report in reports:
                connection.execute(
                    "INSERT INTO analyst_reports VALUES (?, ?, ?, ?)",
                    (
                        report.report_id,
                        report.analyst,
                        report.generated_at.isoformat(),
                        _json(report),
                    ),
                )
            connection.execute(
                "INSERT INTO market_theses VALUES (?, ?, ?)",
                (thesis.thesis_id, thesis.generated_at.isoformat(), _json(thesis)),
            )

    def latest_analyst_reports(self) -> dict[str, AnalystReport]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM analyst_reports current "
                "WHERE NOT EXISTS (SELECT 1 FROM analyst_reports newer "
                "WHERE newer.analyst=current.analyst AND "
                "(newer.generated_at > current.generated_at OR "
                "(newer.generated_at=current.generated_at AND newer.id > current.id)))"
            ).fetchall()
        reports = [
            AnalystReport.model_validate_json(row["payload_json"]) for row in rows
        ]
        return {report.analyst: report for report in reports}

    def latest_market_thesis(self) -> MarketThesis | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM market_theses "
                "ORDER BY generated_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else MarketThesis.model_validate_json(row["payload_json"])

    def record_scoped_analysis(
        self,
        reports: tuple[AnalystReport, AnalystReport, AnalystReport],
        bundle: MarketThesisBundle,
    ) -> None:
        expected = {"market", "news", "sentiment"}
        if {report.analyst for report in reports} != expected:
            raise ValueError("analysis cycle requires market, news, and sentiment reports")
        if set(bundle.source_report_ids) != {report.report_id for report in reports}:
            raise ValueError("thesis bundle must reference the analysis cycle reports")
        with self._connect() as connection:
            for report in reports:
                connection.execute(
                    "INSERT OR IGNORE INTO analyst_reports VALUES (?, ?, ?, ?)",
                    (
                        report.report_id,
                        report.analyst,
                        report.generated_at.isoformat(),
                        _json(report),
                    ),
                )
            connection.execute(
                "INSERT INTO market_thesis_bundles VALUES (?, ?, ?)",
                (bundle.thesis_id, bundle.generated_at.isoformat(), _json(bundle)),
            )

    def latest_market_thesis_bundle(self) -> MarketThesisBundle | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM market_thesis_bundles "
                "ORDER BY generated_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return (
            None
            if row is None
            else MarketThesisBundle.model_validate_json(row["payload_json"])
        )

    def register_scoped_rule(
        self, rule: ScopedRuleCandidate, *, status: str = "queued"
    ) -> None:
        allowed = {
            "queued", "replay_passed", "challenger", "champion",
            "hall_of_fame", "rejected",
        }
        if status not in allowed:
            raise ValueError("invalid rule status")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scoped_rules VALUES (?, ?, ?, ?, ?, ?)",
                (
                    rule.rule_id,
                    rule.scope.value,
                    rule.parent_rule_id,
                    status,
                    _json(rule),
                    rule.created_at.isoformat(),
                ),
            )

    def has_open_scoped_rule_candidate(self, scope: DecisionScope) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM scoped_rules WHERE scope=? AND status IN "
                "('queued', 'replay_passed', 'challenger') LIMIT 1",
                (scope.value,),
            ).fetchone()
        return row is not None

    def scoped_rule_registry(self, scope: DecisionScope) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT champion_id, challenger_id, rollback_id, updated_at "
                "FROM scoped_rule_registry WHERE scope=?",
                (scope.value,),
            ).fetchone()
        if row is None:
            return {"champion_id": None, "challenger_id": None, "rollback_id": None}
        return dict(row)

    def activate_scoped_champion(
        self, scope: DecisionScope, rule_id: str, *, now: datetime
    ) -> dict:
        with self._connect() as connection:
            rule = connection.execute(
                "SELECT scope FROM scoped_rules WHERE id=?", (rule_id,)
            ).fetchone()
            if rule is None or rule["scope"] != scope.value:
                raise ValueError("unknown scoped rule")
            connection.execute(
                "UPDATE scoped_rules SET status='champion' WHERE id=?", (rule_id,)
            )
            connection.execute(
                "INSERT INTO scoped_rule_registry VALUES (?, ?, NULL, NULL, ?) "
                "ON CONFLICT(scope) DO UPDATE SET champion_id=excluded.champion_id, "
                "challenger_id=NULL, rollback_id=NULL, updated_at=excluded.updated_at",
                (scope.value, rule_id, now.isoformat()),
            )
        return self.scoped_rule_registry(scope)

    def load_active_scoped_rule(
        self, scope: DecisionScope
    ) -> ScopedRuleCandidate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT r.payload_json FROM scoped_rules r "
                "JOIN scoped_rule_registry g ON g.champion_id=r.id "
                "WHERE g.scope=?",
                (scope.value,),
            ).fetchone()
        return (
            None
            if row is None
            else ScopedRuleCandidate.model_validate_json(row["payload_json"])
        )

    def activate_champion(self, rule_id: str, *, now: datetime) -> dict:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM rules WHERE id = ?", (rule_id,)).fetchone() is None:
                raise ValueError("unknown rule")
            connection.execute("UPDATE rules SET status = 'champion' WHERE id = ?", (rule_id,))
            connection.execute(
                "INSERT INTO rule_registry VALUES (1, ?, NULL, NULL, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET champion_id=excluded.champion_id, "
                "challenger_id=NULL, rollback_id=NULL, updated_at=excluded.updated_at",
                (rule_id, now.isoformat()),
            )
        return self.rule_registry()

    def set_challenger(self, rule_id: str, *, now: datetime) -> dict:
        with self._connect() as connection:
            registry = connection.execute(
                "SELECT champion_id, challenger_id FROM rule_registry WHERE singleton = 1"
            ).fetchone()
            rule = connection.execute(
                "SELECT parent_id, status FROM rules WHERE id = ?", (rule_id,)
            ).fetchone()
            if registry is None or rule is None:
                raise ValueError("champion and candidate must exist")
            if registry["challenger_id"] is not None:
                raise ValueError("a challenger is already active")
            if rule["status"] != "replay_passed":
                raise ValueError("challenger must pass replay first")
            if rule["parent_id"] != registry["champion_id"]:
                raise ValueError("challenger must descend from the active champion")
            connection.execute("UPDATE rules SET status = 'challenger' WHERE id = ?", (rule_id,))
            connection.execute(
                "UPDATE rule_registry SET challenger_id = ?, updated_at = ? WHERE singleton = 1",
                (rule_id, now.isoformat()),
            )
        return self.rule_registry()

    def promote_challenger(self, *, now: datetime) -> dict:
        with self._connect() as connection:
            registry = connection.execute(
                "SELECT champion_id, challenger_id FROM rule_registry WHERE singleton = 1"
            ).fetchone()
            if registry is None or registry["challenger_id"] is None:
                raise ValueError("no active challenger")
            old, new = registry["champion_id"], registry["challenger_id"]
            connection.execute("UPDATE rules SET status = 'hall_of_fame' WHERE id = ?", (old,))
            connection.execute("UPDATE rules SET status = 'champion' WHERE id = ?", (new,))
            connection.execute(
                "UPDATE rule_registry SET champion_id = ?, challenger_id = NULL, "
                "rollback_id = ?, updated_at = ? WHERE singleton = 1",
                (new, old, now.isoformat()),
            )
        return self.rule_registry()

    def reject_challenger(self, *, now: datetime) -> dict:
        with self._connect() as connection:
            registry = connection.execute(
                "SELECT challenger_id FROM rule_registry WHERE singleton = 1"
            ).fetchone()
            if registry is None or registry["challenger_id"] is None:
                raise ValueError("no active challenger")
            connection.execute(
                "UPDATE rules SET status='rejected' WHERE id=?",
                (registry["challenger_id"],),
            )
            connection.execute(
                "UPDATE rule_registry SET challenger_id=NULL, updated_at=? WHERE singleton=1",
                (now.isoformat(),),
            )
        return self.rule_registry()

    def rollback_champion(self, *, now: datetime) -> dict:
        with self._connect() as connection:
            registry = connection.execute(
                "SELECT champion_id, rollback_id FROM rule_registry WHERE singleton = 1"
            ).fetchone()
            if registry is None or registry["rollback_id"] is None:
                raise ValueError("no rollback target")
            current, previous = registry["champion_id"], registry["rollback_id"]
            connection.execute("UPDATE rules SET status = 'hall_of_fame' WHERE id = ?", (current,))
            connection.execute("UPDATE rules SET status = 'champion' WHERE id = ?", (previous,))
            connection.execute(
                "UPDATE rule_registry SET champion_id = ?, rollback_id = ?, updated_at = ? "
                "WHERE singleton = 1",
                (previous, current, now.isoformat()),
            )
        return self.rule_registry()

    def rule_registry(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT champion_id, challenger_id, rollback_id, updated_at "
                "FROM rule_registry WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return {"champion_id": None, "challenger_id": None, "rollback_id": None}
        return dict(row)

    def load_active_rule(self) -> RuleCandidate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT r.payload_json FROM rules r JOIN rule_registry g "
                "ON g.champion_id = r.id WHERE g.singleton = 1"
            ).fetchone()
        return None if row is None else RuleCandidate.model_validate_json(row["payload_json"])

    def list_pending_notifications(self, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, delivery_key, kind, message, attempts, created_at "
                "FROM notification_outbox WHERE status = 'pending' "
                "ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_notification_delivered(self, notification_id: int, *, delivered_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE notification_outbox SET status='delivered', attempts=attempts+1, "
                "delivered_at=?, last_error=NULL WHERE id=? AND status='pending'",
                (delivered_at.isoformat(), notification_id),
            )

    def mark_notification_failed(self, notification_id: int, *, error_type: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE notification_outbox SET attempts=attempts+1, last_error=? "
                "WHERE id=? AND status='pending'",
                (error_type[:80], notification_id),
            )

    def record_news(self, result: NewsIngestResult) -> None:
        with self._connect() as connection:
            for event in result.accepted_events:
                connection.execute(
                    "INSERT OR IGNORE INTO news_events VALUES (?, ?, ?, ?)",
                    (event.event_id, event.source_id, event.received_at.isoformat(), _json(event)),
                )
            for cluster in result.clusters:
                connection.execute(
                    "INSERT INTO news_clusters VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET verified=excluded.verified, "
                    "last_seen_at=excluded.last_seen_at, payload_json=excluded.payload_json",
                    (
                        cluster.cluster_id,
                        int(cluster.verified),
                        cluster.last_seen_at.isoformat(),
                        _json(cluster),
                    ),
                )

    def list_news_events(self, limit: int = 1000) -> list[NewsEvent]:
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM news_events ORDER BY received_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [NewsEvent.model_validate_json(row["payload_json"]) for row in rows]

    def record_venue_frame(self, frame: VenueMarketFrame) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO venue_market_frames VALUES (?, ?, ?, ?, ?, ?)",
                (
                    frame.frame_id,
                    frame.venue,
                    frame.symbol,
                    frame.event_time.isoformat(),
                    frame.received_at.isoformat(),
                    _json(frame),
                ),
            )

    def venue_frame_count(self, venue: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM venue_market_frames WHERE venue = ?", (venue,)
            ).fetchone()[0]

    def prune_venue_frames(self, *, before: datetime) -> int:
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("retention cutoff must be timezone-aware")
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM venue_market_frames WHERE event_time < ?",
                (before.isoformat(),),
            )
        return cursor.rowcount

    def list_venue_frames(
        self,
        venue: str,
        *,
        symbol: str = "BTCUSDT",
        limit: int = 1000,
    ) -> list[VenueMarketFrame]:
        if not 1 <= limit <= 1_000_000:
            raise ValueError("limit must be between 1 and 1000000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM venue_market_frames "
                "WHERE venue = ? AND symbol = ? "
                "ORDER BY event_time DESC, id DESC LIMIT ?",
                (venue, symbol, limit),
            ).fetchall()
        return [
            VenueMarketFrame.model_validate_json(row["payload_json"])
            for row in reversed(rows)
        ]

    def venue_health(
        self,
        venue: str,
        *,
        now: datetime | None = None,
        window_days: int = 14,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(days=window_days)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total, MAX(received_at) AS last_received_at, "
                "SUM(CASE WHEN event_time >= ? THEN 1 ELSE 0 END) AS window_frames "
                "FROM venue_market_frames WHERE venue = ?",
                (since.isoformat(), venue),
            ).fetchone()
            latest_row = connection.execute(
                "SELECT payload_json FROM venue_market_frames WHERE venue = ? "
                "ORDER BY event_time DESC, id DESC LIMIT 1", (venue,)
            ).fetchone()
        last = row["last_received_at"]
        recent = int(row["window_frames"] or 0)
        expected = window_days * 24 * 60 * 12
        latest = (
            VenueMarketFrame.model_validate_json(latest_row["payload_json"])
            if latest_row else None
        )
        return {
            "venue": venue,
            "total_frames": int(row["total"]),
            "window_days": window_days,
            "window_frames": recent,
            "coverage": min(recent / expected, 1.0),
            "last_received_at": last,
            "age_seconds": (
                max(0.0, (now - datetime.fromisoformat(last)).total_seconds())
                if last else None
            ),
            "latest": (
                {
                    "event_time": latest.event_time.isoformat(),
                    "mark_price": latest.mark_price,
                    "funding_bps_hour": latest.funding_bps_hour,
                    "open_interest_usd": latest.open_interest_usd,
                    "spread_bps": latest.spread_bps,
                    "book_imbalance_10bps": latest.book_imbalance["10"],
                    "metadata_age_seconds": max(
                        0.0,
                        (latest.received_at - latest.metadata_received_at).total_seconds(),
                    ),
                }
                if latest else None
            ),
        }

    def record_cross_venue_evaluation(
        self, evaluation: CrossVenuePromotionEvaluation
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO cross_venue_evaluations VALUES (?, ?, ?, ?)",
                (
                    evaluation.evaluation_id,
                    evaluation.status,
                    evaluation.evaluated_at.isoformat(),
                    _json(evaluation),
                ),
            )

    def latest_cross_venue_evaluation(self) -> CrossVenuePromotionEvaluation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM cross_venue_evaluations "
                "ORDER BY evaluated_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return (
            None if row is None else
            CrossVenuePromotionEvaluation.model_validate_json(row["payload_json"])
        )

    def cross_venue_activation_allowed(self) -> bool:
        latest = self.latest_cross_venue_evaluation()
        return latest is not None and latest.status == "promote"

    def sync_provider_profile(self, profile: ProviderProfile) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO provider_profiles "
                "(id, role, kind, fingerprint, payload_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "role=excluded.role, kind=excluded.kind, "
                "fingerprint=excluded.fingerprint, payload_json=excluded.payload_json, "
                "last_test_status=CASE WHEN provider_profiles.fingerprint=excluded.fingerprint "
                "THEN provider_profiles.last_test_status ELSE NULL END, "
                "last_test_at=CASE WHEN provider_profiles.fingerprint=excluded.fingerprint "
                "THEN provider_profiles.last_test_at ELSE NULL END, "
                "latency_ms=CASE WHEN provider_profiles.fingerprint=excluded.fingerprint "
                "THEN provider_profiles.latency_ms ELSE NULL END, "
                "error_code=CASE WHEN provider_profiles.fingerprint=excluded.fingerprint "
                "THEN provider_profiles.error_code ELSE NULL END, "
                "updated_at=excluded.updated_at",
                (
                    profile.profile_id,
                    profile.role.value,
                    profile.kind.value,
                    profile.fingerprint,
                    _json(profile),
                    profile.updated_at.isoformat(),
                ),
            )

    def provider_profile(self, profile_id: str) -> ProviderProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM provider_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        return None if row is None else ProviderProfile.model_validate_json(row["payload_json"])

    def list_provider_profiles(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json, last_test_status, last_test_at, latency_ms, error_code "
                "FROM provider_profiles ORDER BY role, id"
            ).fetchall()
        return [
            {
                **ProviderProfile.model_validate_json(row["payload_json"]).model_dump(mode="json"),
                "last_test_status": row["last_test_status"],
                "last_test_at": row["last_test_at"],
                "latency_ms": row["latency_ms"],
                "error_code": row["error_code"],
            }
            for row in rows
        ]

    def record_provider_test(
        self,
        profile_id: str,
        *,
        status: str,
        tested_at: datetime,
        latency_ms: int | None,
        error_code: str | None = None,
    ) -> None:
        if status not in {"ok", "error"}:
            raise ValueError("invalid provider test status")
        if status == "ok" and error_code is not None:
            raise ValueError("successful provider test cannot have an error code")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE provider_profiles SET last_test_status=?, last_test_at=?, "
                "latency_ms=?, error_code=? WHERE id=?",
                (status, tested_at.isoformat(), latency_ms, error_code, profile_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("unknown provider profile")

    def activate_provider(
        self,
        role: ProviderRole,
        profile_id: str,
        *,
        actor: str,
        now: datetime,
    ) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role, fingerprint, last_test_status, last_test_at "
                "FROM provider_profiles WHERE id=?",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown provider profile")
            if row["role"] != role.value:
                raise ValueError("provider profile role mismatch")
            tested_at = (
                datetime.fromisoformat(row["last_test_at"])
                if row["last_test_at"] else None
            )
            age = now - tested_at if tested_at else None
            if (
                row["last_test_status"] != "ok"
                or age is None
                or not timedelta(0) <= age <= timedelta(minutes=10)
            ):
                raise ValueError("provider profile requires a recent successful preflight")
            connection.execute(
                "INSERT INTO provider_assignments VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(role) DO UPDATE SET profile_id=excluded.profile_id, "
                "profile_fingerprint=excluded.profile_fingerprint, "
                "activated_at=excluded.activated_at, actor=excluded.actor",
                (role.value, profile_id, row["fingerprint"], now.isoformat(), actor),
            )
            assigned = connection.execute(
                "SELECT * FROM provider_assignments WHERE role=?", (role.value,)
            ).fetchone()
        return dict(assigned)

    def provider_assignment(self, role: ProviderRole) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM provider_assignments WHERE role=?", (role.value,)
            ).fetchone()
        return None if row is None else dict(row)

    def deactivate_provider(self, role: ProviderRole) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM provider_assignments WHERE role=?", (role.value,)
            )

    def delete_provider_profile(self, profile_id: str) -> None:
        with self._connect() as connection:
            assigned = connection.execute(
                "SELECT role FROM provider_assignments WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if assigned is not None:
                raise ValueError(
                    f"provider profile is active for {assigned['role']}; deactivate it first"
                )
            cursor = connection.execute(
                "DELETE FROM provider_profiles WHERE id=?", (profile_id,)
            )
            if cursor.rowcount != 1:
                raise ValueError("unknown provider profile")

    def record_model_call(self, call: ModelCallRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO model_calls VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    call.call_id,
                    call.role.value,
                    call.profile_id,
                    call.started_at.isoformat(),
                    call.status,
                    call.cost_usd,
                    _json(call),
                ),
            )

    def list_model_calls(self, limit: int = 100) -> list[ModelCallRecord]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM model_calls "
                "ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [ModelCallRecord.model_validate_json(row["payload_json"]) for row in rows]

    def latest_successful_model_call(self, workflow: str) -> ModelCallRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM model_calls "
                "WHERE status='success' AND json_extract(payload_json, '$.workflow')=? "
                "ORDER BY julianday(started_at) DESC, id DESC LIMIT 1",
                (workflow,),
            ).fetchone()
        return None if row is None else ModelCallRecord.model_validate_json(row["payload_json"])

    def model_cost_since(self, since: datetime) -> float:
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("cost boundary must be timezone-aware")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM model_calls "
                "WHERE julianday(started_at) >= julianday(?) AND cost_usd IS NOT NULL",
                (since.isoformat(),),
            ).fetchone()
        return round(float(row["total"]), 12)
