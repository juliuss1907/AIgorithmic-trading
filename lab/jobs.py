"""Durable sequential backtest jobs with restart-safe state transitions."""

import argparse
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from lab.contracts import ExperimentSpec
from lab.data import DATA, ROOT, resolve_snapshot
from lab.datasets import DatasetCatalog


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class IdempotencyConflict(ValueError):
    pass


class JobQueue:
    def __init__(self, database=None, runs_dir=None, catalog=None):
        self.database = Path(database or ROOT / "state/lab.sqlite3").resolve()
        self.runs_dir = Path(runs_dir or ROOT / "runs").resolve()
        self.catalog = catalog or DatasetCatalog(DATA)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = self.database.parent / "job-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    entry_point TEXT NOT NULL,
                    idempotency_key TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'completed', 'failed', 'interrupted')
                    ),
                    config_json TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    output_relative TEXT NOT NULL UNIQUE,
                    result_run_id TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN idempotency_key TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key "
                "ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )

    def _event(self, job, event, level="info", **fields):
        record = {
            "timestamp": utc_now(), "level": level, "event": event,
            "request_id": job["id"], "entry_point": job["entry_point"], **fields,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with (self.log_dir / f"{job['id']}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    def enqueue(self, config, entry_point, idempotency_key=None):
        config = ExperimentSpec.model_validate(config)
        snapshot = resolve_snapshot(config, self.catalog)
        frame, _ = self.catalog.load(snapshot.id)
        for name, (start, _) in config.periods.items():
            first = frame.index.searchsorted(pd.Timestamp(start))
            if first < config.strategy.slow_window:
                raise ValueError(
                    f"Period {name!r} needs at least {config.strategy.slow_window} warmup sessions"
                )
            if first >= len(frame) - 1:
                raise ValueError(f"Period {name!r} needs at least two evaluation sessions")
        frozen = config.model_copy(update={"dataset_id": snapshot.id})
        config_json = json.dumps(frozen.to_json_dict(), separators=(",", ":"))
        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not 8 <= len(idempotency_key) <= 128:
                raise ValueError("Idempotency-Key must contain 8 to 128 characters")
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
            if existing is not None:
                if existing["config_json"] != config_json:
                    raise IdempotencyConflict("Idempotency-Key belongs to a different config")
                return self._decode(existing)
        job_id = uuid.uuid4().hex
        created_at = utc_now()
        values = (
            job_id, entry_point, idempotency_key, "queued", config_json,
            snapshot.id, job_id, None, None, None, created_at, None, None,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO jobs (id, entry_point, idempotency_key, status, config_json, "
                    "dataset_id, output_relative, result_run_id, error_type, error_message, "
                    "created_at, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
        except sqlite3.IntegrityError:
            if idempotency_key is None:
                raise
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
            if existing is None or existing["config_json"] != config_json:
                raise IdempotencyConflict("Idempotency-Key belongs to a different config")
            return self._decode(existing)
        job = self.get(job_id)
        self._event(job, "job_queued", status="queued", dataset_id=snapshot.id)
        return job

    @staticmethod
    def _decode(row):
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        return result

    def get(self, job_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown job: {job_id}")
        return self._decode(row)

    def list(self):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._decode(row) for row in rows]

    def claim_next(self):
        connection = self._connect()
        try:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            started_at = utc_now()
            connection.execute(
                "UPDATE jobs SET status = 'running', started_at = ? "
                "WHERE id = ? AND status = 'queued'", (started_at, row["id"]),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        job = self.get(row["id"])
        self._event(job, "job_started", status="running")
        return job

    def complete(self, job_id, result_run_id):
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'completed', result_run_id = ?, finished_at = ? "
                "WHERE id = ? AND status = 'running'", (result_run_id, utc_now(), job_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Only a running job can complete")
        job = self.get(job_id)
        self._event(job, "job_completed", status="completed", result_run_id=result_run_id)
        return job

    def fail(self, job_id, error):
        message = str(error)[:500]
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'failed', error_type = ?, error_message = ?, "
                "finished_at = ? WHERE id = ? AND status = 'running'",
                (type(error).__name__, message, utc_now(), job_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Only a running job can fail")
        job = self.get(job_id)
        self._event(job, "job_failed", level="error", status="failed",
                    error_type=type(error).__name__)
        return job

    def recover_interrupted(self):
        with self._connect() as connection:
            ids = [row[0] for row in connection.execute(
                "SELECT id FROM jobs WHERE status = 'running'"
            )]
            connection.executemany(
                "UPDATE jobs SET status = 'interrupted', error_type = 'WorkerRestart', "
                "error_message = 'Worker stopped before completion', finished_at = ? WHERE id = ?",
                [(utc_now(), job_id) for job_id in ids],
            )
        for job_id in ids:
            job = self.get(job_id)
            self._event(job, "job_interrupted", level="error", status="interrupted",
                        error_type="WorkerRestart")
        return ids

    def retry(self, job_id, entry_point):
        previous = self.get(job_id)
        if previous["status"] not in {"failed", "interrupted"}:
            raise ValueError("Only a failed or interrupted job can be retried")
        return self.enqueue(ExperimentSpec.model_validate(previous["config"]), entry_point)

    def output_path(self, job):
        path = (self.runs_dir / job["output_relative"]).resolve()
        try:
            path.relative_to(self.runs_dir)
        except ValueError as exc:
            raise ValueError("Unsafe job output path") from exc
        return path

    def read_log(self, job_id):
        self.get(job_id)
        path = self.log_dir / f"{job_id}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]


class JobWorker:
    def __init__(self, queue=None, execute=None):
        self.queue = queue or JobQueue()
        self.execute = execute or self._execute_backtest
        self.queue.recover_interrupted()

    def _execute_backtest(self, config, output):
        from lab.experiment import run
        from lab.report import generate
        from lab.store import RunStore

        run(config, output, catalog=self.queue.catalog)
        generate(output)
        registered = RunStore(self.queue.database, self.queue.runs_dir).import_run(output)
        return registered["id"]

    def run_once(self):
        job = self.queue.claim_next()
        if job is None:
            return None
        try:
            config = ExperimentSpec.model_validate(job["config"])
            result_run_id = self.execute(config, self.queue.output_path(job))
            return self.queue.complete(job["id"], result_run_id)
        except Exception as exc:
            return self.queue.fail(job["id"], exc)

    def run_forever(self, poll_seconds=1.0):
        while True:
            if self.run_once() is None:
                time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="Sequential System Trading Lab worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    worker = JobWorker()
    if args.once:
        worker.run_once()
    else:
        try:
            worker.run_forever(args.poll_seconds)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
