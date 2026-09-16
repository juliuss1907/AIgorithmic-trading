"""Local BTCUSDT paper broker. This module has no live-order transport."""

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import pandas as pd

from lab.paper_promotion import build_campaign_contract, contract_sha256
from lab.strategy import RuleSignalEngine, position_sizing_from_dict, strategy_from_dict


DEFAULT_RULES = {"quantity_step": "0.00001000", "min_notional": "5.00000000"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class PaperTradingService:
    def __init__(self, database, log_dir=None):
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir = Path(log_dir or self.database.parent / "paper-logs").resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    id TEXT PRIMARY KEY, strategy_json TEXT NOT NULL,
                    position_sizing_json TEXT NOT NULL DEFAULT '{"family":"fixed"}',
                    start_policy TEXT NOT NULL DEFAULT 'immediate',
                    entry_armed INTEGER NOT NULL DEFAULT 1,
                    dataset_snapshot_id TEXT NOT NULL,
                    initial_cash REAL NOT NULL, cash REAL NOT NULL,
                    btc_quantity REAL NOT NULL, average_cost REAL NOT NULL,
                    equity REAL NOT NULL, peak_equity REAL NOT NULL, drawdown REAL NOT NULL,
                    max_target_weight REAL NOT NULL, halt_drawdown REAL NOT NULL,
                    quantity_step TEXT NOT NULL, min_notional TEXT NOT NULL,
                    fee_bps REAL NOT NULL, slippage_bps REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','halted')),
                    halt_reason TEXT, last_candle TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_cycles (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
                    candle_date TEXT NOT NULL, dataset_snapshot_id TEXT NOT NULL,
                    entry_point TEXT NOT NULL,
                    status TEXT NOT NULL, signal REAL NOT NULL, evidence_json TEXT NOT NULL,
                    reconciliation_ok INTEGER NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(account_id,candle_date)
                );
                CREATE TABLE IF NOT EXISTS paper_signals (
                    cycle_id TEXT PRIMARY KEY REFERENCES paper_cycles(id),
                    target REAL NOT NULL, evidence_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_order_intents (
                    id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL UNIQUE REFERENCES paper_cycles(id),
                    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                    quantity REAL NOT NULL, quote_price REAL NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_fills (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
                    cycle_id TEXT NOT NULL UNIQUE REFERENCES paper_cycles(id),
                    intent_id TEXT NOT NULL UNIQUE REFERENCES paper_order_intents(id),
                    side TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL,
                    fee REAL NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
                    cycle_id TEXT, kind TEXT NOT NULL, cash_delta REAL NOT NULL,
                    btc_delta REAL NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_halts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
                    cycle_id TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_campaigns (
                    account_id TEXT PRIMARY KEY REFERENCES paper_accounts(id),
                    contract_json TEXT NOT NULL,
                    contract_sha256 TEXT NOT NULL UNIQUE,
                    target_cycles INTEGER NOT NULL DEFAULT 56,
                    started_at TEXT,
                    finalized_at TEXT,
                    final_review_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_incidents (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
                    candle_date TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    details TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open','recovered','acknowledged')),
                    operator_note TEXT,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    UNIQUE(account_id,candle_date,kind)
                );
                CREATE TABLE IF NOT EXISTS paper_notifications (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
                    event_key TEXT NOT NULL,
                    channel TEXT NOT NULL CHECK(channel IN ('telegram')),
                    kind TEXT NOT NULL CHECK(kind IN ('daily_summary','critical')),
                    message TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','failed','sent')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id,event_key,channel)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_accounts)")}
            if "dataset_snapshot_id" not in columns:
                connection.execute(
                    "ALTER TABLE paper_accounts ADD COLUMN dataset_snapshot_id TEXT NOT NULL "
                    "DEFAULT 'legacy-untracked'"
                )
            if "position_sizing_json" not in columns:
                connection.execute(
                    "ALTER TABLE paper_accounts ADD COLUMN position_sizing_json TEXT NOT NULL "
                    "DEFAULT '{\"family\":\"fixed\"}'"
                )
            if "start_policy" not in columns:
                connection.execute(
                    "ALTER TABLE paper_accounts ADD COLUMN start_policy TEXT NOT NULL DEFAULT 'immediate'"
                )
            if "entry_armed" not in columns:
                connection.execute(
                    "ALTER TABLE paper_accounts ADD COLUMN entry_armed INTEGER NOT NULL DEFAULT 1"
                )
            cycle_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(paper_cycles)")
            }
            if "dataset_snapshot_id" not in cycle_columns:
                connection.execute(
                    "ALTER TABLE paper_cycles ADD COLUMN dataset_snapshot_id TEXT NOT NULL "
                    "DEFAULT 'legacy-untracked'"
                )

    def _event(self, account_id, event, *, cycle_id=None, entry_point=None, **fields):
        record = {
            "timestamp": utc_now(), "level": "info", "event": event,
            "account_id": account_id, "cycle_id": cycle_id,
            "entry_point": entry_point, **fields,
        }
        with (self.log_dir / f"{account_id}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _account(row):
        result = dict(row)
        result["strategy"] = json.loads(result.pop("strategy_json"))
        result["position_sizing"] = json.loads(result.pop("position_sizing_json"))
        result["entry_armed"] = bool(result["entry_armed"])
        return result

    def _prepare_account(
        self, strategy, dataset_snapshot_id, initial_cash, exchange_rules,
        max_target_weight, halt_drawdown, fee_bps, slippage_bps,
        position_sizing, start_policy,
    ):
        strategy = strategy_from_dict(strategy)
        position_sizing = position_sizing_from_dict(position_sizing)
        if start_policy not in {"immediate", "wait_for_new_entry"}:
            raise ValueError("Unsupported paper account start policy")
        if len(dataset_snapshot_id) != 64 or any(c not in "0123456789abcdef" for c in dataset_snapshot_id):
            raise ValueError("Paper account requires a frozen dataset snapshot id")
        rules = exchange_rules or DEFAULT_RULES
        if initial_cash <= 0 or max_target_weight != .5 or halt_drawdown != .20:
            raise ValueError("BTC paper MVP freezes 10,000+ cash, 50% target and 20% halt")
        step = Decimal(str(rules["quantity_step"]))
        minimum = Decimal(str(rules["min_notional"]))
        if step <= 0 or minimum <= 0 or fee_bps != 10 or slippage_bps != 5:
            raise ValueError("Invalid or unfrozen BTC paper execution assumptions")
        strategy_json = json.dumps(strategy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        sizing_json = json.dumps(
            position_sizing.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return strategy_json, sizing_json, rules

    @staticmethod
    def _insert_account(
        connection, account_id, now, strategy_json, sizing_json, start_policy,
        dataset_snapshot_id, initial_cash, rules, max_target_weight,
        halt_drawdown, fee_bps, slippage_bps,
    ):
        entry_armed = start_policy == "immediate"
        connection.execute(
            "INSERT INTO paper_accounts "
            "(id,strategy_json,position_sizing_json,start_policy,entry_armed,dataset_snapshot_id,"
            "initial_cash,cash,btc_quantity,average_cost,"
            "equity,peak_equity,drawdown,max_target_weight,halt_drawdown,quantity_step,min_notional,"
            "fee_bps,slippage_bps,status,halt_reason,last_candle,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (account_id, strategy_json, sizing_json, start_policy, int(entry_armed),
             dataset_snapshot_id, initial_cash, initial_cash, 0, 0,
             initial_cash, initial_cash, 0, max_target_weight, halt_drawdown,
             str(rules["quantity_step"]), str(rules["min_notional"]), fee_bps,
             slippage_bps, "active", None, None, now, now),
        )
        connection.execute(
            "INSERT INTO paper_ledger (account_id,cycle_id,kind,cash_delta,btc_delta,created_at) "
            "VALUES (?,NULL,'deposit',?,0,?)", (account_id, initial_cash, now),
        )

    def create_account(
        self, strategy, *, dataset_snapshot_id, initial_cash=10_000, exchange_rules=None,
        max_target_weight=.5, halt_drawdown=.20, fee_bps=10, slippage_bps=5,
        position_sizing=None, start_policy="immediate",
    ):
        strategy_json, sizing_json, rules = self._prepare_account(
            strategy, dataset_snapshot_id, initial_cash, exchange_rules,
            max_target_weight, halt_drawdown, fee_bps, slippage_bps,
            position_sizing, start_policy,
        )
        account_id = uuid.uuid4().hex
        now = utc_now()
        with self._connect() as connection:
            self._insert_account(
                connection, account_id, now, strategy_json, sizing_json, start_policy,
                dataset_snapshot_id, initial_cash, rules, max_target_weight,
                halt_drawdown, fee_bps, slippage_bps,
            )
        self._event(account_id, "account_created", entry_point="manual")
        return self.get_account(account_id)

    def create_promoted_account(self, promotion_store, **kwargs):
        state = promotion_store.get()
        if not state.get("holdout") or not state["holdout"].get("passed"):
            raise ValueError("Paper account requires a passing holdout")
        lock = state.get("candidate_lock")
        if not lock or lock.get("candidate") != state.get("selected"):
            raise ValueError("Paper account requires a valid candidate lock")
        reserved = {"dataset_snapshot_id", "position_sizing", "start_policy"}.intersection(kwargs)
        if reserved:
            raise ValueError("Promoted account contract cannot be overridden")
        execution = {
            "initial_cash": kwargs.pop("initial_cash", 10_000),
            "exchange_rules": kwargs.pop("exchange_rules", None),
            "max_target_weight": kwargs.pop("max_target_weight", .5),
            "halt_drawdown": kwargs.pop("halt_drawdown", .20),
            "fee_bps": kwargs.pop("fee_bps", 10),
            "slippage_bps": kwargs.pop("slippage_bps", 5),
        }
        if kwargs:
            raise TypeError(f"Unsupported promoted account options: {', '.join(sorted(kwargs))}")
        strategy_json, sizing_json, rules = self._prepare_account(
            lock["strategy"], state["holdout"]["dataset_id"],
            execution["initial_cash"], execution["exchange_rules"],
            execution["max_target_weight"], execution["halt_drawdown"],
            execution["fee_bps"], execution["slippage_bps"],
            lock["position_sizing"], "wait_for_new_entry",
        )
        contract = build_campaign_contract(state, rules)
        expected_execution = contract["execution"]
        actual_execution = {
            "initial_cash": execution["initial_cash"],
            "max_target_weight": execution["max_target_weight"],
            "halt_drawdown": execution["halt_drawdown"],
            "fee_bps": execution["fee_bps"],
            "slippage_bps": execution["slippage_bps"],
            "exchange_rules": rules,
            "start_policy": "wait_for_new_entry",
        }
        if actual_execution != expected_execution:
            raise ValueError("Promoted account execution contract cannot be overridden")
        contract_json = json.dumps(contract, sort_keys=True, separators=(",", ":"))
        fingerprint = contract_sha256(contract)
        account_id = uuid.uuid4().hex
        now = utc_now()
        with self._connect() as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT account_id FROM paper_campaigns WHERE contract_sha256=?", (fingerprint,)
            ).fetchone()
            if existing:
                connection.execute("COMMIT")
                return self.get_account(existing["account_id"])
            self._insert_account(
                connection, account_id, now, strategy_json, sizing_json, "wait_for_new_entry",
                state["holdout"]["dataset_id"], execution["initial_cash"], rules,
                execution["max_target_weight"], execution["halt_drawdown"],
                execution["fee_bps"], execution["slippage_bps"],
            )
            connection.execute(
                "INSERT INTO paper_campaigns "
                "(account_id,contract_json,contract_sha256,target_cycles,created_at) "
                "VALUES (?,?,?,56,?)",
                (account_id, contract_json, fingerprint, now),
            )
            connection.execute("COMMIT")
        self._event(account_id, "promoted_account_created", entry_point="promote-paper")
        return self.get_account(account_id)

    def get_campaign(self, account_id):
        self.get_account(account_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_campaigns WHERE account_id=?", (account_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Paper account has no promoted campaign: {account_id}")
        result = dict(row)
        result["contract"] = json.loads(result.pop("contract_json"))
        final_review_json = result.pop("final_review_json")
        result["final_review"] = json.loads(final_review_json) if final_review_json else None
        return result

    def record_incident(self, account_id, candle_date, kind, details):
        self.get_campaign(account_id)
        incident_id = hashlib.sha256(f"{account_id}|{candle_date}|{kind}".encode()).hexdigest()[:24]
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO paper_incidents "
                "(id,account_id,candle_date,kind,details,status,first_seen_at,updated_at) "
                "VALUES (?,?,?,?,?,'open',?,?) "
                "ON CONFLICT(account_id,candle_date,kind) DO UPDATE SET "
                "details=excluded.details,updated_at=excluded.updated_at",
                (incident_id, account_id, str(candle_date), kind, str(details), now, now),
            )
        return next(item for item in self.list_incidents(account_id) if item["id"] == incident_id)

    def recover_incident(self, account_id, candle_date, kind="data_fetch_failed"):
        with self._connect() as connection:
            connection.execute(
                "UPDATE paper_incidents SET status='recovered',updated_at=? "
                "WHERE account_id=? AND candle_date=? AND kind=? AND status='open'",
                (utc_now(), account_id, str(candle_date), kind),
            )

    def acknowledge_incident(self, account_id, incident_id, note):
        note = note.strip()
        if not note:
            raise ValueError("Incident acknowledgement requires a note")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_incidents WHERE id=? AND account_id=?",
                (incident_id, account_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown paper incident: {incident_id}")
            if row["operator_note"] is not None:
                if row["operator_note"] != note:
                    raise ValueError("Incident acknowledgement is immutable")
                return dict(row)
            now = utc_now()
            connection.execute(
                "UPDATE paper_incidents SET status='acknowledged',operator_note=?,"
                "acknowledged_at=?,updated_at=? WHERE id=?",
                (note, now, now, incident_id),
            )
        return next(item for item in self.list_incidents(account_id) if item["id"] == incident_id)

    def list_incidents(self, account_id):
        self.get_campaign(account_id)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM paper_incidents WHERE account_id=? "
                "ORDER BY candle_date DESC,first_seen_at DESC", (account_id,)
            )]

    def enqueue_notification(self, account_id, event_key, kind, message, channel="telegram"):
        self.get_account(account_id)
        if channel != "telegram":
            raise ValueError("Unsupported notification channel")
        if kind not in {"daily_summary", "critical"}:
            raise ValueError("Unsupported notification kind")
        if not isinstance(event_key, str) or not 1 <= len(event_key) <= 200 or "\n" in event_key:
            raise ValueError("Notification event key must contain 1 to 200 characters")
        if not isinstance(message, str) or not 1 <= len(message) <= 4096:
            raise ValueError("Notification message must contain 1 to 4096 characters")
        notification_id = hashlib.sha256(
            f"{account_id}|{channel}|{event_key}".encode()
        ).hexdigest()[:24]
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO paper_notifications "
                "(id,account_id,event_key,channel,kind,message,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'pending',?,?) "
                "ON CONFLICT(account_id,event_key,channel) DO NOTHING",
                (notification_id, account_id, event_key, channel, kind, message, now, now),
            )
            row = connection.execute(
                "SELECT * FROM paper_notifications WHERE account_id=? AND event_key=? AND channel=?",
                (account_id, event_key, channel),
            ).fetchone()
        result = dict(row)
        if result["kind"] != kind or result["message"] != message:
            raise ValueError("Notification event payload is immutable")
        return result

    def list_pending_notifications(self, channel="telegram"):
        if channel != "telegram":
            raise ValueError("Unsupported notification channel")
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM paper_notifications WHERE channel=? AND status!='sent' "
                "ORDER BY created_at,id", (channel,)
            )]

    def _get_notification(self, notification_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_notifications WHERE id=?", (notification_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown paper notification: {notification_id}")
        return dict(row)

    def mark_notification_sent(self, notification_id):
        notification = self._get_notification(notification_id)
        if notification["status"] == "sent":
            return notification
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE paper_notifications SET status='sent',attempts=attempts+1,last_error=NULL,"
                "sent_at=?,updated_at=? WHERE id=? AND status!='sent'",
                (now, now, notification_id),
            )
        result = self._get_notification(notification_id)
        self._event(
            result["account_id"], "notification_sent", entry_point="paper_worker",
            notification_id=result["id"], notification_kind=result["kind"],
            channel=result["channel"], attempts=result["attempts"],
        )
        return result

    def mark_notification_failed(self, notification_id, error_code):
        notification = self._get_notification(notification_id)
        if notification["status"] == "sent":
            return notification
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z0-9_.-]{1,64}", error_code):
            raise ValueError("Notification error code is invalid")
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE paper_notifications SET status='failed',attempts=attempts+1,last_error=?,"
                "updated_at=? WHERE id=? AND status!='sent'",
                (error_code, now, notification_id),
            )
        result = self._get_notification(notification_id)
        self._event(
            result["account_id"], "notification_failed", entry_point="paper_worker",
            notification_id=result["id"], notification_kind=result["kind"],
            channel=result["channel"], attempts=result["attempts"],
            error_code=error_code, level="warn",
        )
        return result

    def campaign_status(self, account_id, now=None):
        campaign = self.get_campaign(account_id)
        account = self.get_account(account_id)
        incidents = self.list_incidents(account_id)
        with self._connect() as connection:
            successful = connection.execute(
                "SELECT COUNT(*) FROM paper_cycles WHERE account_id=? AND entry_point='scheduler' "
                "AND status='completed' AND reconciliation_ok=1", (account_id,)
            ).fetchone()[0]
            sides = [row[0] for row in connection.execute(
                "SELECT side FROM paper_fills WHERE account_id=? ORDER BY created_at,id", (account_id,)
            )]
        current = now or datetime.now(timezone.utc)
        started = datetime.fromisoformat(campaign["started_at"]) if campaign["started_at"] else None
        elapsed_days = max(0, (current - started).days) if started else 0
        round_trips = min(sides.count("buy"), sides.count("sell"))
        blockers = []
        if started is None:
            blockers.append("bootstrap_pending")
        elif elapsed_days < 56:
            blockers.append("minimum_elapsed_days")
        if successful < campaign["target_cycles"]:
            blockers.append("successful_cycles")
        if any(item["status"] == "open" for item in incidents):
            blockers.append("unacknowledged_incidents")
        if account["status"] == "halted":
            blockers.append("account_halted")
        if round_trips < 1:
            blockers.append("completed_round_trip")
        status = "pending" if started is None else "blocked" if account["status"] == "halted" else (
            "eligible" if not blockers else "running"
        )
        return {
            **campaign, "status": status, "elapsed_days": elapsed_days,
            "successful_cycles": successful,
            "remaining_cycles": max(0, campaign["target_cycles"] - successful),
            "incidents": incidents, "round_trips": round_trips, "blockers": blockers,
        }

    def finalize_campaign(self, account_id):
        campaign = self.get_campaign(account_id)
        if campaign["final_review"] is not None:
            return campaign["final_review"]
        status = self.campaign_status(account_id)
        if status["status"] != "eligible" or status["blockers"]:
            raise ValueError("Paper campaign is not eligible for final review")
        contract = status["contract"]
        review = {
            "account_id": account_id,
            "status": "eligible",
            "contract_sha256": status["contract_sha256"],
            "candidate_run_id": contract["candidate_lock"].get("run_id"),
            "holdout_run_id": contract["holdout"].get("run_id"),
            "successful_cycles": status["successful_cycles"],
            "elapsed_days": status["elapsed_days"],
            "round_trips": status["round_trips"],
            "incident_count": len(status["incidents"]),
            "finalized_at": utc_now(),
        }
        review["review_sha256"] = hashlib.sha256(
            json.dumps(review, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        payload = json.dumps(review, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE paper_campaigns SET final_review_json=?,finalized_at=? "
                "WHERE account_id=? AND final_review_json IS NULL",
                (payload, review["finalized_at"], account_id),
            )
        if cursor.rowcount != 1:
            return self.get_campaign(account_id)["final_review"]
        self._event(account_id, "campaign_finalized", entry_point="paper-review")
        return review

    def get_account(self, account_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_accounts WHERE id=?", (account_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown paper account: {account_id}")
        return self._account(row)

    def list_accounts(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_accounts ORDER BY created_at DESC,id"
            ).fetchall()
        return [self._account(row) for row in rows]

    @staticmethod
    def _rules_match(account, rules):
        return (
            Decimal(account["quantity_step"]) == Decimal(str(rules["quantity_step"]))
            and Decimal(account["min_notional"]) == Decimal(str(rules["min_notional"]))
        )

    @staticmethod
    def _validate_bars(frame):
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(frame) or not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("Invalid closed-candle frame")
        if frame.empty or frame.index.tz is not None or not frame.index.is_monotonic_increasing:
            raise ValueError("Invalid closed-candle frame")
        expected = pd.date_range(frame.index[0], frame.index[-1], freq="D")
        if not frame.index.equals(expected):
            raise ValueError("BTC candle frame has a UTC day gap")
        if frame.index[-1].date() >= datetime.now(timezone.utc).date():
            raise ValueError("Paper cycles require a closed UTC candle")
        if not (frame[["open", "high", "low", "close"]] > 0).all().all():
            raise ValueError("BTC candle prices must be positive")

    @staticmethod
    def _cycle_id(account_id, strategy, candle):
        payload = f"{account_id}|{strategy}|{candle}".encode()
        return hashlib.sha256(payload).hexdigest()[:24]

    @staticmethod
    def _halt(connection, account_id, reason, cycle_id=None):
        now = utc_now()
        connection.execute(
            "UPDATE paper_accounts SET status='halted',halt_reason=?,updated_at=? WHERE id=?",
            (reason, now, account_id),
        )
        connection.execute(
            "INSERT INTO paper_halts (account_id,cycle_id,reason,created_at) VALUES (?,?,?,?)",
            (account_id, cycle_id, reason, now),
        )

    @staticmethod
    def _reconcile(connection, account):
        totals = connection.execute(
            "SELECT COALESCE(SUM(cash_delta),0) cash,COALESCE(SUM(btc_delta),0) btc "
            "FROM paper_ledger WHERE account_id=?", (account["id"],)
        ).fetchone()
        return {
            "ok": abs(totals["cash"] - account["cash"]) <= 1e-7
                  and abs(totals["btc"] - account["btc_quantity"]) <= 1e-10,
            "ledger_cash": totals["cash"], "ledger_btc": totals["btc"],
        }

    def reconcile(self, account_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown paper account: {account_id}")
            return self._reconcile(connection, dict(row))

    @staticmethod
    def _round_quantity(raw, step, price, minimum):
        quantity = (Decimal(str(raw)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        return Decimal(0) if quantity * price < minimum else quantity

    def run_cycle(
        self, account_id, frame, *, bid, ask, exchange_rules=None,
        dataset_snapshot_id=None, entry_point="scheduler",
    ):
        try:
            self._validate_bars(frame)
        except ValueError:
            self.halt_account(account_id, "data_validation_failed")
            raise
        if not (0 < bid <= ask):
            self.halt_account(account_id, "invalid_quote")
            raise ValueError("Expected positive bid not above ask")
        candle = str(frame.index[-1].date())
        account_before = self.get_account(account_id)
        dataset_snapshot_id = dataset_snapshot_id or account_before["dataset_snapshot_id"]
        if len(dataset_snapshot_id) != 64 or any(c not in "0123456789abcdef" for c in dataset_snapshot_id):
            self.halt_account(account_id, "dataset_snapshot_invalid")
            raise ValueError("Paper cycle requires a frozen dataset snapshot id")
        strategy_json = json.dumps(account_before["strategy"], sort_keys=True, separators=(",", ":"))
        cycle_id = self._cycle_id(account_id, strategy_json, candle)
        engine = RuleSignalEngine(
            account_before["strategy"], account_before["max_target_weight"],
            position_sizing=account_before["position_sizing"],
        )
        evidence = engine.evidence(frame).iloc[-1]
        raw_target = float(evidence["target"])
        evidence_json = json.dumps(
            {key: (None if pd.isna(value) else value.item() if hasattr(value, "item") else value)
             for key, value in evidence.items()},
            sort_keys=True, separators=(",", ":"),
        )
        fill_id = None
        with self._connect() as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT id FROM paper_cycles WHERE id=?", (cycle_id,)).fetchone()
            if existing is not None:
                connection.execute("COMMIT")
                return self.get_cycle(cycle_id)
            row = connection.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise KeyError(f"Unknown paper account: {account_id}")
            account = dict(row)
            target = raw_target
            if account["start_policy"] == "wait_for_new_entry" and not account["entry_armed"]:
                target = 0.0
                if raw_target == 0:
                    account["entry_armed"] = 1
                    connection.execute(
                        "UPDATE paper_accounts SET entry_armed=1 WHERE id=?", (account_id,)
                    )
            now = utc_now()
            connection.execute(
                "INSERT INTO paper_cycles "
                "(id,account_id,candle_date,dataset_snapshot_id,entry_point,status,signal,evidence_json,"
                "reconciliation_ok,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (cycle_id, account_id, candle, dataset_snapshot_id, entry_point, "running", target,
                 evidence_json, 0, now),
            )
            connection.execute(
                "INSERT INTO paper_signals VALUES (?,?,?)", (cycle_id, target, evidence_json)
            )
            reason = None
            if not self._reconcile(connection, account)["ok"]:
                reason = "reconciliation_failed"
            rules = exchange_rules or {
                "quantity_step": account["quantity_step"], "min_notional": account["min_notional"]
            }
            if reason is None and not self._rules_match(account, rules):
                reason = "exchange_rules_changed"
            equity = float(account["cash"] + account["btc_quantity"] * bid)
            peak = max(float(account["peak_equity"]), equity)
            drawdown = equity / peak - 1
            connection.execute(
                "UPDATE paper_accounts SET equity=?,peak_equity=?,drawdown=?,last_candle=?,updated_at=? WHERE id=?",
                (equity, peak, drawdown, candle, now, account_id),
            )
            account.update({"equity": equity, "peak_equity": peak, "drawdown": drawdown})
            if reason is None and drawdown <= -float(account["halt_drawdown"]):
                reason = "drawdown_limit"
            if account["status"] == "halted":
                reason = account["halt_reason"] or "account_halted"
            if reason is not None and account["status"] != "halted":
                self._halt(connection, account_id, reason, cycle_id)
            side = None
            if reason is None and target > 0 and account["btc_quantity"] <= 1e-10:
                side = "buy"
            elif reason is None and target == 0 and account["btc_quantity"] > 1e-10:
                side = "sell"
            if side is not None:
                slip = Decimal(str(account["slippage_bps"])) / Decimal("10000")
                fee_rate = Decimal(str(account["fee_bps"])) / Decimal("10000")
                fill_price = Decimal(str(ask if side == "buy" else bid)) * (
                    Decimal(1) + slip if side == "buy" else Decimal(1) - slip
                )
                step = Decimal(account["quantity_step"])
                minimum = Decimal(account["min_notional"])
                quantity = (
                    self._round_quantity(equity * target / float(fill_price), step, fill_price, minimum)
                    if side == "buy" else Decimal(str(account["btc_quantity"]))
                )
                if quantity > 0:
                    intent_id = f"{cycle_id}:intent"
                    fill_id = f"{cycle_id}:fill"
                    fee = quantity * fill_price * fee_rate
                    connection.execute(
                        "INSERT INTO paper_order_intents VALUES (?,?,?,?,?,?)",
                        (intent_id, cycle_id, side, float(quantity), float(ask if side == "buy" else bid), "filled"),
                    )
                    connection.execute(
                        "INSERT INTO paper_fills VALUES (?,?,?,?,?,?,?,?,?)",
                        (fill_id, account_id, cycle_id, intent_id, side, float(quantity),
                         float(fill_price), float(fee), now),
                    )
                    cash_delta = float(-(quantity * fill_price + fee) if side == "buy" else quantity * fill_price - fee)
                    btc_delta = float(quantity if side == "buy" else -quantity)
                    new_cash = account["cash"] + cash_delta
                    new_btc = account["btc_quantity"] + btc_delta
                    average_cost = float(fill_price) if side == "buy" else 0
                    new_equity = new_cash + new_btc * bid
                    new_drawdown = new_equity / peak - 1
                    connection.execute(
                        "INSERT INTO paper_ledger (account_id,cycle_id,kind,cash_delta,btc_delta,created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (account_id, cycle_id, side, cash_delta, btc_delta, now),
                    )
                    connection.execute(
                        "UPDATE paper_accounts SET cash=?,btc_quantity=?,average_cost=?,equity=?,drawdown=?,updated_at=? WHERE id=?",
                        (new_cash, new_btc, average_cost, new_equity, new_drawdown, now, account_id),
                    )
                    account.update({"cash": new_cash, "btc_quantity": new_btc})
            reconciled = self._reconcile(connection, account)["ok"]
            if not reconciled and reason is None:
                self._halt(connection, account_id, "reconciliation_failed", cycle_id)
            status = "completed" if reconciled else "halted"
            connection.execute(
                "UPDATE paper_cycles SET status=?,reconciliation_ok=? WHERE id=?",
                (status, int(reconciled), cycle_id),
            )
            if entry_point == "bootstrap" and reconciled:
                connection.execute(
                    "UPDATE paper_campaigns SET started_at=COALESCE(started_at,?) WHERE account_id=?",
                    (now, account_id),
                )
            connection.execute("COMMIT")
        result = self.get_cycle(cycle_id)
        self._event(
            account_id, "paper_cycle_completed", cycle_id=cycle_id, entry_point=entry_point,
            signal=target, fill_id=fill_id, reconciliation_ok=result["reconciliation_ok"],
            halt_reason=self.get_account(account_id)["halt_reason"],
        )
        return result

    def get_cycle(self, cycle_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM paper_cycles WHERE id=?", (cycle_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown paper cycle: {cycle_id}")
            fill = connection.execute("SELECT * FROM paper_fills WHERE cycle_id=?", (cycle_id,)).fetchone()
        result = dict(row)
        result["reconciliation_ok"] = bool(result["reconciliation_ok"])
        result["evidence"] = json.loads(result.pop("evidence_json"))
        result["fill"] = dict(fill) if fill else None
        return result

    def halt_account(self, account_id, reason="manual_kill_switch"):
        self.get_account(account_id)
        with self._connect() as connection:
            current = connection.execute("SELECT status FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
            if current["status"] != "halted":
                self._halt(connection, account_id, reason)
        self._event(account_id, "account_halted", entry_point="manual", reason=reason)
        return self.get_account(account_id)

    def list_fills(self, account_id):
        self.get_account(account_id)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM paper_fills WHERE account_id=? ORDER BY created_at,id", (account_id,)
            )]

    def list_cycles(self, account_id):
        self.get_account(account_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_cycles WHERE account_id=? ORDER BY candle_date DESC,id", (account_id,)
            ).fetchall()
        return [self.get_cycle(row["id"]) for row in rows]

    def list_signals(self, account_id):
        self.get_account(account_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT s.* FROM paper_signals s JOIN paper_cycles c ON c.id=s.cycle_id "
                "WHERE c.account_id=? ORDER BY c.candle_date DESC", (account_id,)
            ).fetchall()
        return [{**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows]

    def list_intents(self, account_id):
        self.get_account(account_id)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT i.* FROM paper_order_intents i JOIN paper_cycles c ON c.id=i.cycle_id "
                "WHERE c.account_id=? ORDER BY c.candle_date DESC", (account_id,)
            )]

    def list_ledger(self, account_id):
        self.get_account(account_id)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM paper_ledger WHERE account_id=? ORDER BY id", (account_id,)
            )]

    def list_halts(self, account_id):
        self.get_account(account_id)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM paper_halts WHERE account_id=? ORDER BY id DESC", (account_id,)
            )]
