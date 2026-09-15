"""Durable, isolated jobs for downloading validated market-data snapshots."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lab.contracts import DatasetRequest
from lab.data import ROOT, download_market_data, fetch


def now():
    return datetime.now(timezone.utc).isoformat()


class DatasetJobQueue:
    def __init__(self, database=None, catalog=None, downloader=None):
        if catalog is None:
            from lab.datasets import DatasetCatalog
            catalog = DatasetCatalog()
        self.database = Path(database or ROOT / "state/lab.sqlite3").resolve()
        self.catalog = catalog
        self.downloader = downloader or download_market_data
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir = self.database.parent / "dataset-job-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS dataset_jobs (
                    id TEXT PRIMARY KEY, entry_point TEXT NOT NULL, idempotency_key TEXT UNIQUE,
                    status TEXT NOT NULL CHECK (status IN
                        ('queued','running','completed','failed','interrupted')),
                    request_json TEXT NOT NULL, result_dataset_id TEXT, error_type TEXT,
                    error_message TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
                )
            """)

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _decode(row):
        value = dict(row)
        value["request"] = json.loads(value.pop("request_json"))
        return value

    def _event(self, job, event, level="info", **fields):
        record = {"timestamp": now(), "level": level, "event": event,
                  "request_id": job["id"], "entry_point": job["entry_point"], **fields}
        line = json.dumps(record, separators=(",", ":"))
        with (self.log_dir / f"{job['id']}.jsonl").open("a") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    def enqueue(self, request, entry_point, idempotency_key=None):
        request = DatasetRequest.model_validate(request)
        payload = json.dumps(request.model_dump(mode="json"), separators=(",", ":"))
        if idempotency_key:
            idempotency_key = idempotency_key.strip()
            if not 8 <= len(idempotency_key) <= 128:
                raise ValueError("Idempotency-Key must contain 8 to 128 characters")
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM dataset_jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
            if existing:
                if existing["request_json"] != payload:
                    raise ValueError("Idempotency-Key belongs to a different dataset request")
                return self._decode(existing)
        job_id = uuid.uuid4().hex
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO dataset_jobs "
                    "(id,entry_point,idempotency_key,status,request_json,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (job_id, entry_point, idempotency_key, "queued", payload, now()),
                )
        except sqlite3.IntegrityError:
            if idempotency_key is None:
                raise
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM dataset_jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
            if existing is None or existing["request_json"] != payload:
                raise ValueError("Idempotency-Key belongs to a different dataset request")
            return self._decode(existing)
        job = self.get(job_id)
        self._event(job, "dataset_job_queued", symbol=request.symbol, status="queued")
        return job

    def get(self, job_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM dataset_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown dataset job: {job_id}")
        return self._decode(row)

    def claim_next(self):
        connection = self._connect()
        try:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM dataset_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            connection.execute(
                "UPDATE dataset_jobs SET status='running',started_at=? WHERE id=? AND status='queued'",
                (now(), row["id"]),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        job = self.get(row["id"])
        self._event(job, "dataset_job_started", symbol=job["request"]["symbol"], status="running")
        return job

    def complete(self, job_id, dataset_id):
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE dataset_jobs SET status='completed',result_dataset_id=?,finished_at=? "
                "WHERE id=? AND status='running'", (dataset_id, now(), job_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Only a running dataset job can complete")
        job = self.get(job_id)
        self._event(job, "dataset_job_completed", status="completed", dataset_id=dataset_id)
        return job

    def fail(self, job_id, error):
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE dataset_jobs SET status='failed',error_type=?,error_message=?,finished_at=? "
                "WHERE id=? AND status='running'",
                (type(error).__name__, str(error)[:500], now(), job_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Only a running dataset job can fail")
        job = self.get(job_id)
        self._event(job, "dataset_job_failed", level="error", status="failed",
                    error_type=type(error).__name__)
        return job

    def recover_interrupted(self):
        with self._connect() as connection:
            ids = [row[0] for row in connection.execute(
                "SELECT id FROM dataset_jobs WHERE status='running'"
            )]
            connection.executemany(
                "UPDATE dataset_jobs SET status='interrupted',error_type='WorkerRestart',"
                "error_message='Worker stopped before completion',finished_at=? WHERE id=?",
                [(now(), job_id) for job_id in ids],
            )
        for job_id in ids:
            job = self.get(job_id)
            self._event(job, "dataset_job_interrupted", level="error", status="interrupted",
                        error_type="WorkerRestart")
        return ids

    def retry(self, job_id, entry_point):
        previous = self.get(job_id)
        if previous["status"] not in {"failed", "interrupted"}:
            raise ValueError("Only a failed or interrupted dataset job can be retried")
        return self.enqueue(previous["request"], entry_point)


class DatasetWorker:
    def __init__(self, queue=None):
        self.queue = queue or DatasetJobQueue()
        self.queue.recover_interrupted()

    def run_once(self):
        job = self.queue.claim_next()
        if job is None:
            return None
        try:
            request = DatasetRequest.model_validate(job["request"])
            snapshot = fetch(request, self.queue.catalog, self.queue.downloader)
            return self.queue.complete(job["id"], snapshot.id)
        except Exception as exc:
            return self.queue.fail(job["id"], exc)
