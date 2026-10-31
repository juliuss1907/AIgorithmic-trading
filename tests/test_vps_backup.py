import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location("vps_backup", Path(__file__).parents[1]/"scripts/vps_backup.py")
vps_backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vps_backup)


def database(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE t(x)")
    connection.execute("INSERT INTO t VALUES (1)")
    connection.commit()
    connection.close()
    return path


def test_prune_keeps_the_newest_and_removes_only_its_own_prefix(tmp_path):
    for day in range(1, 10):
        (tmp_path/f"intraday-202610{day:02d}T000000Z.sqlite3").write_text("x")
        (tmp_path/f"intraday-202610{day:02d}T000000Z.manifest.json").write_text("{}")
    other = tmp_path/"binance-demo-backup-20261029T1418Z-e80e0ab.sqlite3"
    other.write_text("keep")
    removed = vps_backup.prune(tmp_path, "intraday", keep=7)
    assert removed == ["intraday-20261001T000000Z.sqlite3", "intraday-20261002T000000Z.sqlite3"]
    assert not (tmp_path/"intraday-20261001T000000Z.manifest.json").exists()
    assert len(list(tmp_path.glob("intraday-*.sqlite3"))) == 7 and other.exists()


def test_journal_backup_and_restore_drill_read_the_data_back_and_leave_nothing(tmp_path):
    journal = database(tmp_path/"journal.sqlite3")
    backups = tmp_path/"backups"
    backups.mkdir()
    copy = vps_backup.backup_journal(journal, backups, datetime(2026, 10, 31, 2, tzinfo=timezone.utc))
    assert copy.name == "demo-journal-20261031T020000Z.sqlite3"
    assert vps_backup.restore_drill(copy, backups) == 1
    assert sorted(p.name for p in backups.iterdir()) == [copy.name]


def test_corrupt_backup_fails_the_check(tmp_path):
    broken = tmp_path/"broken.sqlite3"
    broken.write_bytes(b"not a database, not at all"*100)
    with pytest.raises(sqlite3.DatabaseError):
        vps_backup.check_database(broken)


def test_read_env_ignores_comments_and_strips_quotes(tmp_path):
    env = tmp_path/"env"
    env.write_text("# TELEGRAM_ENABLED=false\nTELEGRAM_ENABLED='true'\nTELEGRAM_CHAT_ID=1\n")
    assert vps_backup.read_env(env) == {"TELEGRAM_ENABLED": "true", "TELEGRAM_CHAT_ID": "1"}
