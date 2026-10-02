"""Durable weekly research intents, separate from the source and execution journal."""

from datetime import timedelta
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
from zoneinfo import ZoneInfo

from intraday.assets import ticker_symbol
from intraday.replay_v2.contracts import utc
from intraday.replay_v2.metrics import encoded, fingerprint


VN = ZoneInfo("Asia/Ho_Chi_Minh")


def weekly_slot(now):
    local = utc(now).astimezone(VN)
    return (local-timedelta(days=local.weekday())).date().isoformat()


def due_weekly(now):
    local = utc(now).astimezone(VN)
    return local.weekday() == 0 and local.hour >= 9


class ReviewStore:
    def __init__(self, root):
        self.path = Path(root).resolve()/"confidence"/"reviews.sqlite3"

    @contextmanager
    def connect(self, *, write=False):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError("research review paths cannot be symlinks")
        if write:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            c = sqlite3.connect(self.path, timeout=15)
            c.execute("CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, symbol TEXT NOT NULL, "
                      "week TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, "
                      "payload_json TEXT NOT NULL, checksum TEXT NOT NULL, UNIQUE(symbol,week))")
        else:
            c = sqlite3.connect(self.path.as_uri()+"?mode=ro", uri=True, timeout=15)
            c.execute("PRAGMA query_only=ON")
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if fingerprint(payload) != row["checksum"]:
            raise ValueError("confidence review checksum invalid")
        return {**payload, "review_id": row["id"], "symbol": row["symbol"],
                "week":row["week"], "status":row["status"], "created_at":row["created_at"],
                "research_only":True, "activation_allowed":False}

    def latest(self, symbol):
        symbol = ticker_symbol(symbol)
        if not self.path.exists():
            return None
        with self.connect() as c:
            return self.decode(c.execute("SELECT * FROM reviews WHERE symbol=? ORDER BY week DESC LIMIT 1",
                                         (symbol,)).fetchone())

    def claim(self, symbol, now, payload):
        symbol, week = ticker_symbol(symbol), weekly_slot(now)
        identity = fingerprint({"symbol":symbol,"week":week,"policy":"confidence-v1"})[:32]
        with self.connect(write=True) as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM reviews WHERE symbol=? AND status='pending_review'", (symbol,)).fetchone():
                return False
            changed = c.execute("INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?,?,?)", (
                identity, symbol, week, "running", utc(now).isoformat(), encoded(payload), fingerprint(payload))).rowcount
        return bool(changed)

    def finish(self, symbol, now, payload):
        if payload["status"] not in {"pending_review", "deferred", "reject", "error"}:
            raise ValueError("invalid research review result")
        with self.connect(write=True) as c:
            changed = c.execute("UPDATE reviews SET status=?,payload_json=?,checksum=? "
                "WHERE symbol=? AND week=? AND status='running'", (
                    payload["status"], encoded(payload), fingerprint(payload), ticker_symbol(symbol), weekly_slot(now))).rowcount
            if changed != 1:
                raise ValueError("confidence review intent is missing or already completed")

    def dismiss(self, symbol, review_id, *, now):
        with self.connect(write=True) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM reviews WHERE symbol=? AND id=? AND status='pending_review'",
                            (ticker_symbol(symbol), review_id)).fetchone()
            if not row:
                raise ValueError("no matching pending research proposal")
            payload = self.decode(row)
            payload.update(status="dismissed", dismissed_at=utc(now).isoformat())
            c.execute("UPDATE reviews SET status=?,payload_json=?,checksum=? WHERE id=?",
                      ("dismissed", encoded(payload), fingerprint(payload), review_id))
