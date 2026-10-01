"""Offline, source-immutable rehearsal of the v22 SQLite upgrade."""

import json
import sqlite3
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from intraday.backups import create_backup
from intraday.upgrade_preflight import preflight_upgrade_backup
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _backup(tmp_path: Path, *, version: int = 21) -> Path:
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.executescript(f"""
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '{version}');
            CREATE TABLE asset_daily_candles (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL CHECK (interval='1d'),
                open_time INTEGER NOT NULL,
                close_time INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, open_time)
            );
            CREATE TABLE legacy_journal (
                id INTEGER PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TRIGGER legacy_journal_immutable BEFORE UPDATE ON legacy_journal
                BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            INSERT INTO legacy_journal VALUES (1, 'preserve-this-row');
        """)
        connection.execute(
            "INSERT INTO asset_daily_candles VALUES (?, ?, ?, ?, ?)",
            ("BTCUSDT", "1d", 0, 86_399_999,
             json.dumps([0, "100", "102", "99", "101", "10", 86_399_999])),
        )
    result = create_backup(source, tmp_path / "backups", now=NOW)
    return Path(result["backup"])


def test_preflight_migrates_private_copy_preserves_rows_and_rehearses_restore(tmp_path):
    backup = _backup(tmp_path)
    original = backup.read_bytes()
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    report = preflight_upgrade_backup(backup, work_dir=work_dir)

    assert report["status"] == "pass"
    assert report["source_schema_version"] == 21
    assert report["target_schema_version"] == 23
    assert report["checks"] == {
        "backup_verified": True,
        "migration_integrity": True,
        "foreign_keys": True,
        "data_preserved": True,
        "schema_objects_preserved": True,
        "idempotent": True,
        "restore_rehearsal": True,
        "source_unchanged": True,
    }
    assert report["table_counts_before"]["asset_daily_candles"] == 1
    assert report["table_counts_before"]["legacy_journal"] == 1
    assert report["reason_codes"] == []
    assert "preserve-this-row" not in json.dumps(report)
    assert backup.read_bytes() == original
    assert list(work_dir.iterdir()) == []


def test_preflight_rejects_tampered_backup_without_creating_trial_copy(tmp_path):
    backup = _backup(tmp_path)
    backup.write_bytes(backup.read_bytes() + b"tampered")
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    report = preflight_upgrade_backup(backup, work_dir=work_dir)

    assert report["status"] == "fail"
    assert report["checks"]["backup_verified"] is False
    assert report["checks"]["migration_integrity"] is None
    assert "backup_verification_failed" in report["reason_codes"]
    assert list(work_dir.iterdir()) == []


def test_preflight_rejects_backup_symlink_and_missing_manifest(tmp_path):
    backup = _backup(tmp_path)
    link = tmp_path / "linked.sqlite3"
    link.symlink_to(backup)

    linked_report = preflight_upgrade_backup(link)
    assert linked_report["status"] == "fail"
    assert linked_report["reason_codes"] == ["backup_verification_failed"]

    backup.with_suffix(".manifest.json").unlink()
    missing_manifest_report = preflight_upgrade_backup(backup)
    assert missing_manifest_report["status"] == "fail"
    assert missing_manifest_report["reason_codes"] == ["backup_verification_failed"]


@pytest.mark.parametrize("version", [19, 20, 21])
def test_preflight_supports_legacy_schema_versions_without_touching_source(tmp_path, version):
    backup = _backup(tmp_path, version=version)

    report = preflight_upgrade_backup(backup)

    assert report["status"] == "pass"
    assert report["source_schema_version"] == version
    with sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == str(version)


def test_preflight_accepts_v22_backup_idempotently(tmp_path):
    database = tmp_path / "v22.sqlite3"
    store = IntradayStore(database)
    store.record_asset_candles("ETHUSDT", "4h", [
        [0, "100", "102", "99", "101", "10", 14_399_999]
    ])
    backup = Path(create_backup(database, tmp_path / "backups", now=NOW)["backup"])

    report = preflight_upgrade_backup(backup)

    assert report["status"] == "pass"
    assert report["source_schema_version"] == 23
    assert report["table_counts_before"]["asset_daily_candles"] == 1


def test_preflight_rejects_unsupported_schema_before_migration(tmp_path):
    backup = _backup(tmp_path, version=24)

    report = preflight_upgrade_backup(backup)

    assert report["status"] == "fail"
    assert report["reason_codes"] == ["unsupported_source_schema"]
    assert report["checks"]["migration_integrity"] is None
    assert report["checks"]["source_unchanged"] is True


def test_preflight_rejects_insufficient_work_space_before_copy(tmp_path, monkeypatch):
    import intraday.upgrade_preflight as preflight

    backup = _backup(tmp_path)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    usage = preflight.shutil.disk_usage(work_dir)
    monkeypatch.setattr(preflight.shutil, "disk_usage", lambda _: usage._replace(free=0))

    report = preflight_upgrade_backup(backup, work_dir=work_dir)

    assert report["status"] == "fail"
    assert report["reason_codes"] == ["insufficient_work_space"]
    assert report["checks"]["migration_integrity"] is None
    assert list(work_dir.iterdir()) == []


def test_preflight_rejects_symlink_work_directory(tmp_path):
    backup = _backup(tmp_path)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    link = tmp_path / "linked-work"
    link.symlink_to(work_dir, target_is_directory=True)

    report = preflight_upgrade_backup(backup, work_dir=link)

    assert report["status"] == "fail"
    assert report["reason_codes"] == ["preflight_permissionerror"]
    assert list(work_dir.iterdir()) == []


def test_preflight_detects_migration_error_without_touching_backup(tmp_path, monkeypatch):
    import intraday.upgrade_preflight as preflight

    backup = _backup(tmp_path)
    original = backup.read_bytes()
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    def broken_migration(_path):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(preflight, "IntradayStore", broken_migration)
    report = preflight_upgrade_backup(backup, work_dir=work_dir)

    assert report["status"] == "fail"
    assert "preflight_runtimeerror" in report["reason_codes"]
    assert report["checks"]["source_unchanged"] is True
    assert backup.read_bytes() == original
    assert list(work_dir.iterdir()) == []


def test_preflight_detects_lost_data_and_trigger_on_migrated_copy(tmp_path, monkeypatch):
    import intraday.upgrade_preflight as preflight

    backup = _backup(tmp_path)
    real_store = IntradayStore

    def losing_migration(path):
        result = real_store(path)
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM legacy_journal WHERE id=1")
            connection.execute("DROP TRIGGER IF EXISTS legacy_journal_immutable")
        return result

    monkeypatch.setattr(preflight, "IntradayStore", losing_migration)
    report = preflight_upgrade_backup(backup)

    assert report["status"] == "fail"
    assert report["checks"]["data_preserved"] is False
    assert report["checks"]["schema_objects_preserved"] is False
    assert report["checks"]["source_unchanged"] is True


def test_preflight_command_writes_private_report_without_overwrite(tmp_path, monkeypatch, capsys):
    from intraday.__main__ import main

    backup = _backup(tmp_path)
    output = tmp_path / "preflight.json"
    monkeypatch.setattr(sys, "argv", [
        "aigt", "upgrade", "preflight", "--backup", str(backup),
        "--output", str(output),
    ])

    main()

    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "pass"
    assert json.loads(output.read_text(encoding="utf-8")) == printed
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(SystemExit):
        main()


def test_preflight_command_emits_failure_report_and_nonzero_exit(tmp_path, monkeypatch, capsys):
    from intraday.__main__ import main

    backup = _backup(tmp_path)
    backup.write_bytes(backup.read_bytes() + b"tampered")
    monkeypatch.setattr(sys, "argv", [
        "aigt", "upgrade", "preflight", "--backup", str(backup),
    ])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "fail"
    assert printed["reason_codes"] == ["backup_verification_failed"]
