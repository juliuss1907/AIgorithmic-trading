"""SQLite metadata index for immutable research runs and mutable notes."""

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from lab.contracts import RunSummary
from lab.data import ROOT, digest
from lab.datasets import DatasetCatalog


class ArtifactChanged(ValueError):
    pass


class RunStore:
    REQUIRED_ARTIFACTS = ("summary.json", "provenance.json", "report.md", "equity.png")
    CASE_ARTIFACTS = ("summary.json", "fills-exact.csv", "equity.csv", "audit.json")

    def __init__(self, database=None, runs_dir=None):
        self.database = Path(database or ROOT / "state/lab.sqlite3").resolve()
        self.runs_dir = Path(runs_dir or ROOT / "runs").resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    run_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_label TEXT NOT NULL,
                    dataset_id TEXT,
                    parent_run_id TEXT,
                    relative_path TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK (status = 'completed'),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    label TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    UNIQUE (run_id, relative_path)
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            if "parent_run_id" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN parent_run_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS runs_parent_run_id ON runs(parent_run_id)"
            )

    def _inside_runs(self, path):
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(self.runs_dir)
        except ValueError as exc:
            raise ValueError("Run must stay inside the configured runs directory") from exc
        return resolved, str(relative)

    @staticmethod
    def _artifact_id(run_id, relative_path):
        return hashlib.sha256(f"{run_id}:{relative_path}".encode()).hexdigest()[:20]

    @staticmethod
    def _dataset_id(provenance, experiment):
        if experiment.get("dataset_id"):
            return experiment["dataset_id"]
        manifest = provenance.get("data", {})
        hashes = manifest.get("files", {})
        raw = next((value for name, value in hashes.items() if name.endswith("raw.csv")), None)
        adjusted = next((value for name, value in hashes.items() if name.endswith("adjusted.csv")), None)
        required = {"symbol", "source", "start", "end", "rows", "adjustment"}
        if raw and adjusted and required.issubset(manifest):
            return DatasetCatalog.identity(manifest, raw, adjusted)
        return None

    @staticmethod
    def _metadata(provenance, run_name):
        experiment = provenance.get("experiment", {})
        symbol = experiment.get("symbol", "UNKNOWN")
        strategy = experiment.get("strategy", {})
        fast = strategy.get("fast_window", experiment.get("fast_window", "?"))
        slow = strategy.get("slow_window", experiment.get("slow_window", "?"))
        return {
            "run_name": run_name,
            "title": experiment.get("title", f"{symbol} SMA {fast}/{slow} pilot"),
            "hypothesis": experiment.get(
                "hypothesis", "Run cũ được nhập vào thư viện; giả thuyết chưa có trong metadata."
            ),
            "symbol": symbol,
            "strategy_label": f"SMA {fast}/{slow}",
            "dataset_id": RunStore._dataset_id(provenance, experiment),
            "parent_run_id": experiment.get("parent_run_id"),
        }

    @staticmethod
    def _media_type(path):
        return {
            ".json": "application/json",
            ".csv": "text/csv",
            ".md": "text/markdown",
            ".png": "image/png",
        }.get(path.suffix, "application/octet-stream")

    def import_run(self, path):
        root, relative_root = self._inside_runs(path)
        missing = [name for name in self.REQUIRED_ARTIFACTS if not (root / name).is_file()]
        if missing:
            raise ValueError(f"Run is incomplete; missing artifacts: {', '.join(missing)}")
        summary = json.loads((root / "summary.json").read_text())
        if not summary:
            raise ValueError("Run summary is empty")
        for value in summary.values():
            RunSummary.model_validate(value)
        provenance = json.loads((root / "provenance.json").read_text())
        run_id = hashlib.sha256(relative_root.encode()).hexdigest()[:20]
        metadata = self._metadata(provenance, root.name)
        created_at = datetime.fromtimestamp(root.stat().st_mtime, timezone.utc).isoformat()
        artifact_paths = [root / name for name in self.REQUIRED_ARTIFACTS]
        for case in summary:
            parts = Path(case).parts
            if len(parts) != 3 or any(part in {"", ".", ".."} for part in parts):
                raise ValueError(f"Unsafe case name in summary: {case}")
            artifact_paths.extend(
                path for name in self.CASE_ARTIFACTS if (path := root / case / name).is_file()
            )
        values = (
            run_id, metadata["run_name"], metadata["title"], metadata["hypothesis"],
            metadata["symbol"], metadata["strategy_label"], metadata["dataset_id"],
            metadata["parent_run_id"], relative_root, "completed", created_at,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO runs "
                "(id, run_name, title, hypothesis, symbol, strategy_label, dataset_id, "
                "parent_run_id, relative_path, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values
            )
            for artifact_path in artifact_paths:
                relative_path = str(artifact_path.relative_to(root))
                connection.execute(
                    "INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        self._artifact_id(run_id, relative_path), run_id, relative_path,
                        self._media_type(artifact_path), relative_path, digest(artifact_path),
                    ),
                )
        return self.get_run(run_id)

    def sync_runs(self):
        imported, skipped = [], []
        for path in sorted(self.runs_dir.iterdir()):
            if not path.is_dir():
                continue
            try:
                imported.append(self.import_run(path)["id"])
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                skipped.append({"run_name": path.name, "reason": str(exc)})
        return {"imported": imported, "skipped": skipped}

    def list_runs(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return dict(row)

    def list_artifacts(self, run_id):
        self.get_run(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, label, media_type, relative_path FROM artifacts "
                "WHERE run_id = ? ORDER BY relative_path", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def artifact(self, run_id, artifact_id):
        run = self.get_run(run_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id = ? AND run_id = ?", (artifact_id, run_id)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        run_root, _ = self._inside_runs(self.runs_dir / run["relative_path"])
        path = (run_root / row["relative_path"]).resolve()
        try:
            path.relative_to(run_root)
        except ValueError as exc:
            raise ValueError("Unsafe artifact path") from exc
        if not path.is_file() or digest(path) != row["sha256"]:
            raise ArtifactChanged("Artifact is missing or changed after registration")
        return path, dict(row)

    def detail(self, run_id):
        run = self.get_run(run_id)
        artifacts = self.list_artifacts(run_id)
        by_path = {item["relative_path"]: item for item in artifacts}
        summary_id = by_path["summary.json"]["id"]
        summary_path, _ = self.artifact(run_id, summary_id)
        summary = json.loads(summary_path.read_text())
        sma_cases = [key for key in summary if key.endswith("/sma")]
        preferred = next((key for key in sma_cases if key.startswith("evaluation/5bps/")), None)
        preferred = preferred or next((key for key in sma_cases if "/5bps/" in key), None)
        preferred = preferred or (sma_cases[-1] if sma_cases else None)
        fills = []
        if preferred:
            relative = f"{preferred}/fills-exact.csv"
            if relative in by_path:
                path, _ = self.artifact(run_id, by_path[relative]["id"])
                with path.open(newline="") as stream:
                    fills = list(csv.DictReader(stream))
        return {"run": run, "summary": summary, "fills": fills, "artifacts": artifacts,
                "featured_case": preferred}

    def add_note(self, run_id, body):
        self.get_run(run_id)
        body = body.strip()
        if not body:
            raise ValueError("Note cannot be empty")
        if len(body) > 2000:
            raise ValueError("Note cannot exceed 2000 characters")
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO notes (run_id, body, created_at) VALUES (?, ?, ?)",
                (run_id, body, created_at),
            )
            note_id = cursor.lastrowid
        return {"id": note_id, "run_id": run_id, "body": body, "created_at": created_at}

    def list_notes(self, run_id):
        self.get_run(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, run_id, body, created_at FROM notes WHERE run_id = ? ORDER BY id DESC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]
