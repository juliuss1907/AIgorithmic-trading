#!/usr/bin/env python3
"""Daily VPS backup of the source database and the Demo journal, with retention and a weekly restore drill.

Runs on the VPS host with the standard library only. A failure exits nonzero and sends a Telegram alert.
Scheduled copies live in their own folder, so retention never touches a manual pre-deploy backup.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import urllib.parse
import urllib.request

KEEP = 7
DRILL_WEEKDAY = 6  # Sunday


def run_admin(project, backups, *arguments):
    command = ["docker", "compose", "--env-file", str(project/".env.intraday"),
               "-f", str(project/"deploy/intraday/compose.yaml"), "--profile", "admin", "run", "--rm", "--no-deps", "-T",
               "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{backups}:/backups", "admin", *arguments]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, check=True)
    return json.loads(result.stdout[result.stdout.index("{"):])


def check_database(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        connection.close()
    if result != "ok" or not tables:
        raise RuntimeError(f"database check failed: {path.name}")
    return tables


def backup_journal(journal, backups, now):
    target = backups/f"demo-journal-{now:%Y%m%dT%H%M%SZ}.sqlite3"
    source = sqlite3.connect(f"file:{journal}?mode=ro", uri=True, timeout=60)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    check_database(target)
    return target


def prune(backups, prefix, keep=KEEP):
    """Oldest first by the timestamp in the name; a source backup goes with its manifest."""
    removed = []
    for path in sorted(backups.glob(prefix+"-*.sqlite3"))[:-keep]:
        path.unlink()
        path.with_suffix(".manifest.json").unlink(missing_ok=True)
        # Opening a WAL-mode backup read-only leaves these beside it.
        for suffix in ("-shm","-wal"):
            path.with_name(path.name+suffix).unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def restore_drill(backup, parent):
    """Restore into an empty database through SQLite and read it back; the temporary copy is always removed."""
    with tempfile.TemporaryDirectory(dir=parent, prefix="restore-drill-") as folder:
        restored = Path(folder)/"restored.sqlite3"
        source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        destination = sqlite3.connect(restored)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        return check_database(restored)


def read_env(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def alert(project, text):
    """Best effort: the nonzero exit stays the primary signal. The token never reaches logs or arguments."""
    try:
        env = read_env(project/".env.intraday")
        if env.get("TELEGRAM_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
            return
        data = urllib.parse.urlencode({"chat_id": env["TELEGRAM_CHAT_ID"], "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendMessage", data=data, timeout=20)
    except Exception as error:
        print(json.dumps({"alert_failed": type(error).__name__}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home()/"AIgorithmic-trading")
    parser.add_argument("--backups", type=Path, default=Path.home()/".local/state/aigorithmic-trading/backups/daily")
    parser.add_argument("--drill", action="store_true", help="run the restore drill regardless of the weekday")
    arguments = parser.parse_args()
    now = datetime.now(timezone.utc)
    try:
        arguments.backups.mkdir(parents=True, exist_ok=True)
        created = run_admin(arguments.project, arguments.backups, "backup", "create", "--output-dir", "/backups")
        name = Path(created["backup"]).name
        run_admin(arguments.project, arguments.backups, "backup", "verify", f"/backups/{name}")
        journal = backup_journal(arguments.project/"state/execution/binance-demo.sqlite3", arguments.backups, now)
        report = {"source": name, "journal": journal.name}
        if arguments.drill or now.weekday() == DRILL_WEEKDAY:
            report["restore_drill_tables"] = restore_drill(arguments.backups/name, arguments.backups)
        report["removed"] = prune(arguments.backups, "intraday")+prune(arguments.backups, "demo-journal")
        print(json.dumps(report))
    except Exception as error:
        alert(arguments.project, f"[VPS] Backup lỗi: {type(error).__name__}")
        raise


if __name__ == "__main__":
    main()
