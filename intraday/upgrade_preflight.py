"""Rehearse a SQLite schema upgrade using a verified backup, never the source."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile

from intraday.backups import verify_backup
from intraday.store import IntradayStore


TARGET_SCHEMA_VERSION = 23
SUPPORTED_SOURCE_VERSIONS = frozenset({19, 20, 21, 22, 23})
_SEEDABLE_TABLES = frozenset({"asset_scope_lifecycle", "asset_scoped_rule_registry"})
_CHECK_NAMES = (
    "backup_verified", "migration_integrity", "foreign_keys", "data_preserved",
    "schema_objects_preserved", "idempotent", "restore_rehearsal",
    "source_unchanged",
)
_HASH_MODULUS = 1 << 256


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _read_only(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    immutable_option = "&immutable=1" if immutable else ""
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro{immutable_option}", uri=True,
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def _row_hash(row: tuple) -> int:
    encoded = json.dumps(
        list(row), sort_keys=True, separators=(",", ":"),
        default=lambda value: {"blob_hex": value.hex()}
        if isinstance(value, bytes) else str(value),
    ).encode()
    return int.from_bytes(hashlib.sha256(encoded).digest(), "big")


def _fingerprint(
    connection: sqlite3.Connection, table: str, columns: tuple[str, ...],
    *, keep_rows: bool = False,
) -> dict:
    selection = ", ".join(_quoted(column) for column in columns)
    query = f"SELECT {selection} FROM {_quoted(table)}"
    if table == "schema_meta":
        query += " WHERE key!='schema_version'"
    count = total = xor = 0
    rows = Counter() if keep_rows else None
    for row in connection.execute(query):
        value = _row_hash(row)
        count += 1
        total = (total + value) % _HASH_MODULUS
        xor ^= value
        if rows is not None:
            rows[value] += 1
    return {"count": count, "sum": total, "xor": xor, "rows": rows}


def _catalog(connection: sqlite3.Connection) -> dict:
    tables = {}
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        columns = tuple(row[1] for row in connection.execute(
            f"PRAGMA table_info({_quoted(table)})"
        ))
        tables[table] = {
            "columns": columns,
            "fingerprint": _fingerprint(
                connection, table, columns,
                keep_rows=table in _SEEDABLE_TABLES,
            ),
        }
    objects = {
        (kind, name): sql for kind, name, sql in connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('index', 'trigger') AND sql IS NOT NULL"
        )
    }
    return {"tables": tables, "objects": objects}


def _preserved(
    source: dict, current: dict, destination: sqlite3.Connection,
) -> tuple[bool, bool]:
    data_ok = True
    for table, details in source["tables"].items():
        after = current["tables"].get(table)
        if after is None or not set(details["columns"]).issubset(after["columns"]):
            data_ok = False
            continue
        before_fingerprint = details["fingerprint"]
        after_fingerprint = _fingerprint(
            destination, table, details["columns"],
            keep_rows=table in _SEEDABLE_TABLES,
        )
        if table in _SEEDABLE_TABLES:
            data_ok &= not (before_fingerprint["rows"] - after_fingerprint["rows"])
        else:
            data_ok &= before_fingerprint == after_fingerprint
    objects_ok = all(
        current["objects"].get(key) == sql
        for key, sql in source["objects"].items()
    )
    return data_ok, objects_ok


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    return int(row[0]) if row else -1


def _integrity_and_foreign_keys(connection: sqlite3.Connection) -> tuple[bool, bool]:
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    return integrity == ["ok"], not any(connection.execute("PRAGMA foreign_key_check"))


def _work_directory(path: str | Path | None) -> Path:
    work = Path(path).expanduser() if path is not None else Path(tempfile.gettempdir())
    details = work.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise PermissionError("preflight work directory must be a real directory")
    return work


class _PreflightFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def preflight_upgrade_backup(
    backup: str | Path, *, work_dir: str | Path | None = None,
) -> dict:
    """Verify, migrate and restore private copies; never open the backup writable."""
    source = Path(backup).expanduser().absolute()
    report = {
        "report_schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "fail",
        "source_schema_version": None,
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "backup_sha256": None,
        "backup_bytes": None,
        "table_counts_before": {},
        "checks": {name: None for name in _CHECK_NAMES},
        "reason_codes": [],
    }
    expected_hash: str | None = None
    try:
        verified = verify_backup(source)
        expected_hash = str(verified["sha256"])
        report["backup_sha256"] = expected_hash
        report["backup_bytes"] = int(verified["bytes"])
        report["checks"]["backup_verified"] = True

        with closing(_read_only(source, immutable=True)) as connection:
            source_version = _schema_version(connection)
            report["source_schema_version"] = source_version
            if source_version not in SUPPORTED_SOURCE_VERSIONS:
                raise _PreflightFailed("unsupported_source_schema")
            before = _catalog(connection)
            report["table_counts_before"] = {
                table: details["fingerprint"]["count"]
                for table, details in before["tables"].items()
            }

        work = _work_directory(work_dir)
        minimum_free = max(256 * 1024 * 1024, 3 * int(verified["bytes"]))
        if shutil.disk_usage(work).free < minimum_free:
            raise _PreflightFailed("insufficient_work_space")
        with tempfile.TemporaryDirectory(prefix="aigt-upgrade-", dir=work) as scratch:
            trial = Path(scratch) / "migrated.sqlite3"
            restored = Path(scratch) / "restored.sqlite3"
            shutil.copyfile(source, trial)
            trial.chmod(0o600)
            if _sha256(trial) != expected_hash:
                raise _PreflightFailed("trial_copy_mismatch")

            IntradayStore(trial)
            with closing(_read_only(trial)) as connection:
                integrity, foreign_keys = _integrity_and_foreign_keys(connection)
                report["checks"]["migration_integrity"] = (
                    integrity and _schema_version(connection) == TARGET_SCHEMA_VERSION
                )
                report["checks"]["foreign_keys"] = foreign_keys
                after = _catalog(connection)
                data_ok, objects_ok = _preserved(before, after, connection)
                report["checks"]["data_preserved"] = data_ok
                report["checks"]["schema_objects_preserved"] = objects_ok

            IntradayStore(trial)
            with closing(_read_only(trial)) as connection:
                report["checks"]["idempotent"] = (
                    _schema_version(connection) == TARGET_SCHEMA_VERSION
                    and _catalog(connection) == after
                )

            shutil.copyfile(source, restored)
            restored.chmod(0o600)
            with closing(_read_only(restored, immutable=True)) as connection:
                restored_integrity, restored_foreign_keys = (
                    _integrity_and_foreign_keys(connection)
                )
                report["checks"]["restore_rehearsal"] = (
                    _sha256(restored) == expected_hash
                    and _schema_version(connection) == source_version
                    and _catalog(connection) == before
                    and restored_integrity and restored_foreign_keys
                )
    except _PreflightFailed as error:
        report["reason_codes"].append(error.reason)
    except Exception as error:
        if report["checks"]["backup_verified"] is None:
            report["checks"]["backup_verified"] = False
        report["reason_codes"].append(
            "backup_verification_failed"
            if report["checks"]["backup_verified"] is False
            else f"preflight_{type(error).__name__.lower()}"
        )
    finally:
        if expected_hash is not None:
            try:
                report["checks"]["source_unchanged"] = _sha256(source) == expected_hash
            except OSError:
                report["checks"]["source_unchanged"] = False

    for name, passed in report["checks"].items():
        if passed is False and name not in {
            "backup_verified", "source_unchanged"
        } and report["checks"]["backup_verified"] is True:
            reason = f"{name}_failed"
            if reason not in report["reason_codes"]:
                report["reason_codes"].append(reason)
    if report["checks"]["source_unchanged"] is False and expected_hash is not None:
        report["reason_codes"].append("source_changed")
    if all(passed is True for passed in report["checks"].values()):
        report["status"] = "pass"
        report["reason_codes"] = []
    return report
