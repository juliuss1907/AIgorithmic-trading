"""Isolated execution evidence; persist first and never blindly retry a mutation."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from intraday.execution.contracts import AccountRef, ExecutionAdapter, ExecutionUnavailable, OrderIntent, OrderUpdate


class ExecutionJournal:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "execution_control" not in tables:
                raise ValueError("existing database is not an execution journal; use a separate file")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS execution_control (
                    account TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_orders (
                    account TEXT NOT NULL, id TEXT NOT NULL, intent TEXT NOT NULL,
                    latest TEXT NOT NULL, PRIMARY KEY (account, id)
                );
                CREATE TABLE IF NOT EXISTS execution_fills (
                    account TEXT NOT NULL, id TEXT NOT NULL, intent_id TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY (account, id)
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                    at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_portfolios (
                    account TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def lock(self):
        descriptor = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ExecutionUnavailable("another execution operation holds the journal lock") from None
            yield
        finally:
            os.close(descriptor)

    def control(self, account: AccountRef) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM execution_control WHERE account=?", (account.key,)).fetchone()
        return json.loads(row[0]) if row else None

    def portfolio(self, account: AccountRef):
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM execution_portfolios WHERE account=?", (account.key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_portfolio(self, account, payload, *, now, kind):
        encoded = json.dumps(payload, sort_keys=True)
        with self.connect() as connection:
            connection.execute("INSERT INTO execution_portfolios VALUES (?,?) ON CONFLICT(account) DO UPDATE SET payload=excluded.payload",
                               (account.key, encoded))
            connection.execute("INSERT INTO execution_events(account,at,kind,payload) VALUES (?,?,?,?)",
                               (account.key, now.isoformat(), kind, encoded))

    def spot_inventory(self, account, symbol, *, require_valued_fees=True):
        """Only journal-owned fills count. Never derive ownership from exchange balances."""
        from decimal import Decimal
        from intraday.execution.contracts import ExecutionFill
        if account.market != "spot":
            raise ValueError("Spot inventory requires a Spot account namespace")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT o.intent,f.payload FROM execution_fills f JOIN execution_orders o "
                "ON f.account=o.account AND f.intent_id=o.id WHERE f.account=? ORDER BY f.rowid", (account.key,)
            ).fetchall()
        quantity, cash, cost, gross_cost = Decimal(0), Decimal(0), Decimal(0), Decimal(0)
        for row in rows:
            intent = OrderIntent.model_validate_json(row[0])
            if intent.symbol != symbol:
                continue
            fill = ExecutionFill.model_validate_json(row[1])
            if require_valued_fees and fill.commission_asset not in {symbol[:-4], "USDT"} and fill.commission:
                raise ExecutionUnavailable("Spot third-asset fee requires operator reconciliation")
            base_fee = fill.commission if fill.commission_asset == symbol[:-4] else Decimal(0)
            quote_fee = fill.commission if fill.commission_asset == "USDT" else Decimal(0)
            notional = fill.quantity * fill.price
            if intent.side == "BUY":
                quantity += fill.quantity-base_fee
                gross_cost += (fill.quantity-base_fee)*fill.price
                cost += notional+quote_fee
                cash -= notional+quote_fee
            else:
                sold = fill.quantity+base_fee
                if sold > quantity:
                    raise ExecutionUnavailable("Spot sell exceeds managed inventory")
                cost *= (quantity-sold)/quantity
                gross_cost *= (quantity-sold)/quantity
                quantity -= sold
                cash += notional-quote_fee
        return {"quantity": quantity, "cash_flow": cash, "cost_basis": cost,
                "entry_price": gross_cost/quantity if quantity else Decimal(0)}

    def save_control(self, account: AccountRef, control: dict, *, kind: str, now: datetime):
        encoded = json.dumps(control, sort_keys=True)
        with self.connect() as connection:
            connection.execute("INSERT INTO execution_control VALUES (?, ?) ON CONFLICT(account) DO UPDATE SET payload=excluded.payload",
                               (account.key, encoded))
            connection.execute("INSERT INTO execution_events(account, at, kind, payload) VALUES (?, ?, ?, ?)",
                               (account.key, now.isoformat(), kind, encoded))

    def prepare(self, intent: OrderIntent) -> bool:
        """Atomic reservation. A crash after reservation requires reconciliation, not replay."""
        encoded = intent.model_dump_json()
        unknown = OrderUpdate(intent_id=intent.intent_id, status="UNKNOWN", received_at=intent.created_at)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT intent FROM execution_orders WHERE account=? AND id=?",
                                     (intent.account.key, intent.intent_id)).fetchone()
            if row:
                if OrderIntent.model_validate_json(row[0]) != intent:
                    raise ValueError("intent id reused with a different payload")
                return False
            connection.execute("INSERT INTO execution_orders VALUES (?, ?, ?, ?)",
                               (intent.account.key, intent.intent_id, encoded, unknown.model_dump_json()))
            return True

    def order(self, account: AccountRef, intent_id: str):
        with self.connect() as connection:
            row = connection.execute("SELECT intent, latest FROM execution_orders WHERE account=? AND id=?",
                                     (account.key, intent_id)).fetchone()
        return (OrderIntent.model_validate_json(row[0]), OrderUpdate.model_validate_json(row[1])) if row else None

    def orders(self, account: AccountRef):
        with self.connect() as connection:
            rows = connection.execute("SELECT intent, latest FROM execution_orders WHERE account=? ORDER BY rowid", (account.key,)).fetchall()
        return [(OrderIntent.model_validate_json(row[0]), OrderUpdate.model_validate_json(row[1])) for row in rows]

    def cash_pnl(self, account: AccountRef):
        from decimal import Decimal
        from intraday.execution.contracts import ExecutionFill
        with self.connect() as connection:
            rows = connection.execute("SELECT payload FROM execution_fills WHERE account=?", (account.key,)).fetchall()
        fills = [ExecutionFill.model_validate_json(row[0]) for row in rows]
        if any(fill.commission_asset != "USDT" for fill in fills):
            raise ExecutionUnavailable("non-USDT commission requires operator reconciliation")
        return sum((fill.realized_pnl - fill.commission for fill in fills), Decimal(0))

    def record(self, intent: OrderIntent, update: OrderUpdate):
        if update.intent_id != intent.intent_id:
            raise ValueError("order update identity mismatch")
        with self.connect() as connection:
            previous = connection.execute("SELECT intent, latest FROM execution_orders WHERE account=? AND id=?",
                                          (intent.account.key, intent.intent_id)).fetchone()
            if not previous or OrderIntent.model_validate_json(previous[0]) != intent:
                raise ValueError("order update requires its persisted intent")
            old = OrderUpdate.model_validate_json(previous[1])
            if update.executed_quantity < old.executed_quantity:
                raise ExecutionUnavailable("order execution quantity regressed")
            if old.terminal and update.status != old.status:
                raise ExecutionUnavailable("terminal order state regressed")
            for fill in update.fills:
                row = connection.execute("SELECT intent_id, payload FROM execution_fills WHERE account=? AND id=?",
                                         (intent.account.key, fill.fill_id)).fetchone()
                if row and (row[0] != intent.intent_id or json.loads(row[1]) != fill.model_dump(mode="json")):
                    raise ExecutionUnavailable("conflicting execution fill identity")
                connection.execute("INSERT OR IGNORE INTO execution_fills VALUES (?, ?, ?, ?)",
                                   (intent.account.key, fill.fill_id, intent.intent_id, fill.model_dump_json()))
            connection.execute("UPDATE execution_orders SET latest=? WHERE account=? AND id=?",
                               (update.model_dump_json(), intent.account.key, intent.intent_id))

    def status(self):
        with self.connect() as connection:
            accounts = connection.execute("SELECT account, payload FROM execution_control ORDER BY account").fetchall()
            pending = connection.execute("SELECT account, id, latest FROM execution_orders ORDER BY rowid DESC LIMIT 25").fetchall()
            count = connection.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0]
            portfolios = connection.execute("SELECT account,payload FROM execution_portfolios ORDER BY account").fetchall()
        return {"database": str(self.path), "accounts": {row[0]: json.loads(row[1]) for row in accounts},
                "portfolios": {row[0]: json.loads(row[1]) for row in portfolios},
                "recent_orders": [{"account": row[0], "id": row[1], "update": json.loads(row[2])} for row in pending],
                "fill_count": count}


class OrderCoordinator:
    def __init__(self, journal: ExecutionJournal, adapter: ExecutionAdapter):
        self.journal = journal
        self.adapter = adapter

    def submit(self, intent: OrderIntent):
        if intent.account != self.adapter.account_ref:
            raise ValueError("coordinator account mismatch")
        if not self.journal.prepare(intent):
            return self.reconcile(intent)
        try:
            update = self.adapter.submit(intent)
        except Exception:
            update = OrderUpdate(intent_id=intent.intent_id, status="UNKNOWN", received_at=datetime.now(timezone.utc))
        self.journal.record(intent, update)
        return update

    def reconcile(self, intent: OrderIntent):
        previous = self.journal.order(intent.account, intent.intent_id)
        if previous is None:
            raise ValueError("cannot reconcile an unjournaled order")
        try:
            update = self.adapter.query(intent)
        except Exception:
            update = None
        if update is None:
            return previous[1]
        self.journal.record(intent, update)
        return update

    def cancel(self, intent: OrderIntent):
        # Do not delete local evidence. Lost cancel ACK still requires querying the order.
        if self.journal.order(intent.account, intent.intent_id) is None:
            raise ValueError("cannot cancel an unjournaled order")
        try:
            update = self.adapter.cancel(intent)
        except Exception:
            return self.reconcile(intent)
        self.journal.record(intent, update)
        return update
