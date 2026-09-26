import json
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from intraday.backups import create_backup, verify_backup


NOW = datetime(2026, 9, 26, 1, 2, 3, tzinfo=timezone.utc)


def _source_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY, payload TEXT)")
    connection.execute("INSERT INTO journal (payload) VALUES ('before-backup')")
    connection.execute(
        "CREATE TRIGGER journal_append_only BEFORE UPDATE ON journal "
        "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
    )
    connection.commit()
    return connection


def test_create_backup_copies_live_wal_database_and_writes_private_manifest(tmp_path):
    source = tmp_path / "state" / "intraday.sqlite3"
    source.parent.mkdir()
    writer = _source_database(source)
    output_dir = tmp_path / "backups"

    try:
        result = create_backup(source, output_dir, now=NOW)
    finally:
        writer.close()

    backup = Path(result["backup"])
    manifest = Path(result["manifest"])
    assert result == {
        "status": "created",
        "backup": str(backup),
        "manifest": str(manifest),
        "created_at": "2026-09-26T01:02:03Z",
        "bytes": backup.stat().st_size,
        "sha256": result["sha256"],
        "integrity": "ok",
    }
    assert backup.name == "intraday-20260926T010203000000Z.sqlite3"
    assert manifest == backup.with_suffix(".manifest.json")
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600

    with sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True) as copied:
        assert copied.execute("SELECT payload FROM journal").fetchall() == [
            ("before-backup",)
        ]
    with sqlite3.connect(backup) as copied:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            copied.execute("UPDATE journal SET payload='changed'")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload == {
        "artifact_version": 1,
        "backup_file": backup.name,
        "bytes": backup.stat().st_size,
        "created_at": "2026-09-26T01:02:03Z",
        "integrity": "ok",
        "sha256": result["sha256"],
    }


def test_verify_backup_detects_tampering_without_modifying_artifact(tmp_path):
    source = tmp_path / "intraday.sqlite3"
    _source_database(source).close()
    created = create_backup(source, tmp_path / "backups", now=NOW)
    backup = Path(created["backup"])

    verified = verify_backup(backup)

    assert verified == {
        "status": "ok",
        "backup": str(backup),
        "manifest": created["manifest"],
        "created_at": created["created_at"],
        "bytes": created["bytes"],
        "sha256": created["sha256"],
        "integrity": "ok",
    }

    backup.write_bytes(backup.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="backup size does not match manifest"):
        verify_backup(backup)


def test_verify_backup_rejects_same_size_checksum_change_and_invalid_manifest(tmp_path):
    source = tmp_path / "intraday.sqlite3"
    _source_database(source).close()
    created = create_backup(source, tmp_path / "backups", now=NOW)
    backup = Path(created["backup"])
    original = backup.read_bytes()
    backup.write_bytes(bytes([original[0] ^ 1]) + original[1:])

    with pytest.raises(ValueError, match="checksum does not match manifest"):
        verify_backup(backup)

    backup.write_bytes(original)
    Path(created["manifest"]).write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="backup manifest is invalid"):
        verify_backup(backup)


def test_create_backup_never_overwrites_and_cleans_temporary_files_on_error(tmp_path):
    source = tmp_path / "intraday.sqlite3"
    _source_database(source).close()
    output_dir = tmp_path / "backups"
    first = create_backup(source, output_dir, now=NOW)
    original = Path(first["backup"]).read_bytes()

    with pytest.raises(FileExistsError, match="already exists"):
        create_backup(source, output_dir, now=NOW)

    assert Path(first["backup"]).read_bytes() == original
    assert not list(output_dir.glob(".*.tmp"))

    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"not a sqlite database")
    with pytest.raises(ValueError, match="database backup failed"):
        create_backup(broken, output_dir, now=NOW.replace(microsecond=1))
    assert not list(output_dir.glob(".*.tmp"))


def test_backup_refuses_symlink_source_and_verify_refuses_symlink_artifact(tmp_path):
    source = tmp_path / "intraday.sqlite3"
    _source_database(source).close()
    source_link = tmp_path / "source-link.sqlite3"
    source_link.symlink_to(source)

    with pytest.raises(PermissionError, match="source database must be a regular file"):
        create_backup(source_link, tmp_path / "backups", now=NOW)

    created = create_backup(source, tmp_path / "backups", now=NOW)
    backup_link = tmp_path / "backup-link.sqlite3"
    backup_link.symlink_to(Path(created["backup"]))
    with pytest.raises(PermissionError, match="backup must be a regular file"):
        verify_backup(backup_link)
