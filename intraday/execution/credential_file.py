"""Private credential storage and guarded rotation; never writes source evidence."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager, nullcontext
from decimal import Decimal
from pathlib import Path

from intraday.execution.contracts import OrderUpdate


class ConnectError(ValueError):
    """Only application-owned, secret-free messages may reach the terminal."""


@contextmanager
def _lock(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise ConnectError("private owned lock file required")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConnectError("another execution or connect operation is running; retry later") from None
        yield
    finally:
        os.close(descriptor)


@contextmanager
def credential_lock(path):
    if (path.suffix != ".json" or path.name.startswith(".env")
            or path.parent.resolve() in {Path.cwd().resolve(), Path.home().resolve(), Path("/")}):
        raise ConnectError("use a dedicated secrets directory and JSON file, not a project root or .env")
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ConnectError("credential paths must not contain symlinks")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = path.parent.stat()
    if directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) != 0o700:
        raise ConnectError("secrets directory must be owned and 0700; set permissions on that directory explicitly")
    with _lock(Path(str(path) + ".lock")):
        yield


def save_credentials(path, credentials, *, replace):
    # A private same-directory temporary file makes publication atomic.
    descriptor, temporary = tempfile.mkstemp(prefix=".credential-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump({"api_key": credentials.api_key.get_secret_value(),
                       "api_secret": credentials.api_secret.get_secret_value()}, output)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # Unlike replace(), link() refuses to overwrite a concurrently created file.
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def rotation_guard(database, credentials, *, probe):
    database = database.absolute()
    if any(item.is_symlink() for item in (database, *database.parents)):
        raise ConnectError("execution database path must not contain symlinks")
    # No DB initialization/migration, and no source store construction.
    lock = _lock(Path(str(database) + ".lock")) if database.exists() else nullcontext()
    with lock:
        if database.exists():
            metadata = database.stat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise ConnectError("owned execution journal required")
            try:
                connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            except sqlite3.Error:
                raise ConnectError("execution journal unavailable; Replace/Clear refused") from None
            try:
                namespaces = (credentials.account_ref.key, credentials.account_ref.key+":spot", credentials.account_ref.key+":perp")
                rows = connection.execute("SELECT payload FROM execution_control WHERE account IN (?,?,?)", namespaces).fetchall()
                tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "execution_portfolios" in tables:
                    rows += connection.execute("SELECT payload FROM execution_portfolios WHERE account=?", (namespaces[0],)).fetchall()
                for row in rows:
                    control = json.loads(row[0])
                    if not isinstance(control, dict) or control.get("paused") is not True:
                        raise ConnectError("pause and flatten the existing Demo campaign before Replace/Clear")
                orders = connection.execute("SELECT intent,latest FROM execution_orders WHERE account IN (?,?,?)", namespaces).fetchall()
                updates = [OrderUpdate.model_validate_json(row[1]) for row in orders]
                if any(not update.terminal or update.executed_quantity != sum(
                        (fill.quantity for fill in update.fills), Decimal(0)) for update in updates):
                    raise ConnectError("unresolved execution intents block Replace/Clear; reconcile first")
                from intraday.execution.contracts import OrderIntent
                managed = {}
                for row, update in zip(orders, updates):
                    intent = OrderIntent.model_validate_json(row[0])
                    if intent.market != "spot":
                        continue
                    delta = sum((f.quantity for f in update.fills), Decimal(0))*(1 if intent.side == "BUY" else -1)
                    delta -= sum((f.commission for f in update.fills if f.commission_asset == intent.symbol[:-4]), Decimal(0))
                    managed[intent.symbol] = managed.get(intent.symbol, Decimal(0))+delta
                if any(qty for qty in managed.values()):
                    raise ConnectError("managed Spot inventory blocks Replace/Clear; flatten and reconcile first")
            except sqlite3.Error:
                raise ConnectError("execution journal unavailable; Replace/Clear refused") from None
            finally:
                connection.close()
        report = probe(credentials)
        if "markets" in report and (set(report["markets"]) != {"spot","perp"}
                                    or any(m.get("status") != "connected" for m in report["markets"].values())):
            raise ConnectError("both Spot and Perp reads must succeed before Replace/Clear")
        if report["positions"] or report["open_orders"]:
            raise ConnectError("open positions/orders block Replace/Clear; flatten and reconcile first")
        yield
