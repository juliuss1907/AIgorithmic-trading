"""Consistent, verifiable SQLite backup artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ARTIFACT_VERSION = 1
_COPY_BUFFER_SIZE = 1024 * 1024


def _absolute(path: str | Path) -> Path:
    value = Path(path).expanduser()
    return value if value.is_absolute() else Path.cwd() / value


def _require_regular_file(path: Path, *, label: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise FileNotFoundError(f"{label} does not exist: {path}") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise PermissionError(f"{label} must be a regular file: {path}")


def prepare_backup_directory(path: str | Path) -> Path:
    path = _absolute(path)
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise PermissionError(f"backup output must be a regular directory: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _integrity(connection: sqlite3.Connection) -> str:
    rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    return "ok" if rows == ["ok"] else "; ".join(str(row) for row in rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_COPY_BUFFER_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_timestamp(value: datetime) -> tuple[str, str]:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("backup time must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    return (
        utc.strftime("%Y%m%dT%H%M%S%fZ"),
        utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_without_overwrite(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError as error:
        raise FileExistsError(f"backup artifact already exists: {destination}") from error
    temporary.unlink()


def create_backup(
    source: str | Path,
    output_dir: str | Path,
    *,
    now: datetime | None = None,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> dict[str, object]:
    """Create and verify a consistent online copy of a live SQLite database."""

    if owner_uid is not None and owner_uid < 0:
        raise ValueError("backup owner uid must not be negative")
    if owner_gid is not None and owner_gid < 0:
        raise ValueError("backup owner gid must not be negative")
    source_path = _absolute(source)
    output_path = _absolute(output_dir)
    _require_regular_file(source_path, label="source database")
    prepare_backup_directory(output_path)
    filename_time, created_at = _utc_timestamp(now or datetime.now(timezone.utc))
    backup_path = output_path / f"intraday-{filename_time}.sqlite3"
    manifest_path = backup_path.with_suffix(".manifest.json")
    if backup_path.exists() or manifest_path.exists():
        raise FileExistsError(f"backup artifact already exists: {backup_path}")

    backup_descriptor, backup_temporary_name = tempfile.mkstemp(
        prefix=".intraday-backup.", suffix=".tmp", dir=output_path
    )
    os.close(backup_descriptor)
    backup_temporary = Path(backup_temporary_name)
    manifest_temporary: Path | None = None
    backup_published = False
    manifest_published = False
    try:
        source_uri = f"{source_path.as_uri()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as source_connection:
            with sqlite3.connect(backup_temporary) as destination_connection:
                source_connection.backup(destination_connection)
                integrity = _integrity(destination_connection)
        if integrity != "ok":
            raise ValueError(f"backup integrity check failed: {integrity}")

        backup_temporary.chmod(0o600)
        if owner_uid is not None or owner_gid is not None:
            os.chown(
                backup_temporary,
                owner_uid if owner_uid is not None else -1,
                owner_gid if owner_gid is not None else -1,
            )
        _fsync(backup_temporary)
        size = backup_temporary.stat().st_size
        checksum = _sha256(backup_temporary)
        manifest_payload = {
            "artifact_version": ARTIFACT_VERSION,
            "backup_file": backup_path.name,
            "bytes": size,
            "created_at": created_at,
            "integrity": integrity,
            "sha256": checksum,
        }
        manifest_descriptor, manifest_temporary_name = tempfile.mkstemp(
            prefix=".intraday-manifest.", suffix=".tmp", dir=output_path
        )
        manifest_temporary = Path(manifest_temporary_name)
        os.fchmod(manifest_descriptor, 0o600)
        with os.fdopen(manifest_descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest_payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if owner_uid is not None or owner_gid is not None:
            os.chown(
                manifest_temporary,
                owner_uid if owner_uid is not None else -1,
                owner_gid if owner_gid is not None else -1,
            )

        _publish_without_overwrite(backup_temporary, backup_path)
        backup_published = True
        _publish_without_overwrite(manifest_temporary, manifest_path)
        manifest_published = True
        _fsync(output_path)
    except sqlite3.DatabaseError as error:
        raise ValueError(f"database backup failed: {error}") from error
    finally:
        backup_temporary.unlink(missing_ok=True)
        if manifest_temporary is not None:
            manifest_temporary.unlink(missing_ok=True)
        if backup_published and not manifest_published:
            backup_path.unlink(missing_ok=True)

    return {
        "status": "created",
        "backup": str(backup_path),
        "manifest": str(manifest_path),
        "created_at": created_at,
        "bytes": size,
        "sha256": checksum,
        "integrity": integrity,
    }


def verify_backup(backup: str | Path) -> dict[str, object]:
    """Verify one published backup without modifying it."""

    backup_path = _absolute(backup)
    _require_regular_file(backup_path, label="backup")
    manifest_path = backup_path.with_suffix(".manifest.json")
    _require_regular_file(manifest_path, label="backup manifest")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"backup manifest is invalid: {manifest_path}") from error
    expected_fields = {
        "artifact_version",
        "backup_file",
        "bytes",
        "created_at",
        "integrity",
        "sha256",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("backup manifest has an unsupported shape")
    if payload["artifact_version"] != ARTIFACT_VERSION:
        raise ValueError("backup manifest version is unsupported")
    if payload["backup_file"] != backup_path.name:
        raise ValueError("backup filename does not match manifest")
    if payload["integrity"] != "ok":
        raise ValueError("backup manifest does not record successful integrity")
    if payload["bytes"] != backup_path.stat().st_size:
        raise ValueError("backup size does not match manifest")
    checksum = _sha256(backup_path)
    if payload["sha256"] != checksum:
        raise ValueError("backup checksum does not match manifest")

    try:
        uri = f"{backup_path.as_uri()}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True) as connection:
            integrity = _integrity(connection)
    except sqlite3.DatabaseError as error:
        raise ValueError(f"backup database is invalid: {error}") from error
    if integrity != "ok":
        raise ValueError(f"backup integrity check failed: {integrity}")

    return {
        "status": "ok",
        "backup": str(backup_path),
        "manifest": str(manifest_path),
        "created_at": payload["created_at"],
        "bytes": payload["bytes"],
        "sha256": checksum,
        "integrity": integrity,
    }
