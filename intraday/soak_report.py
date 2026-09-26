"""Bounded, read-only operational report for portfolio soak readiness."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = "1"


def _fields(payload: dict[str, Any] | None, names: tuple[str, ...]) -> dict | None:
    if payload is None:
        return None
    return {name: payload.get(name) for name in names}


def _evaluation(payload: dict[str, Any] | None) -> dict | None:
    return _fields(
        payload,
        (
            "evaluation_id",
            "evidence_version",
            "status",
            "started_at",
            "evaluated_at",
            "duration_hours",
            "sample_counts",
            "availability",
            "hard_risk_violations",
            "reason_codes",
        ),
    )


def _providers(payload: dict[str, Any]) -> dict[str, dict]:
    projected = {}
    for role, provider in payload.items():
        latest_call = _fields(
            provider.get("latest_call"),
            (
                "workflow",
                "role",
                "profile_id",
                "model",
                "status",
                "started_at",
                "completed_at",
                "latency_ms",
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "error_code",
            ),
        )
        projected[role] = {
            **_fields(
                provider,
                ("active", "profile_id", "kind", "model", "preflight_status"),
            ),
            "latest_call": latest_call,
        }
    return projected


def _recommended_action(snapshot: dict, database: dict) -> str:
    status = snapshot["status"]["code"]
    soak = snapshot["soak"]
    preview = soak["preview"]
    persisted = soak.get("persisted_evaluation")
    if status == "PAPER_ACTIVE":
        return "none"
    if (
        status == "DEGRADED"
        or preview["status"] == "reject"
        or database["integrity"] != "ok"
    ):
        return "investigate"
    if persisted is not None and persisted["status"] == "pass":
        return "request_paper_activation"
    if preview["status"] == "pass":
        return "run_evaluation"
    return "wait"


def build_soak_readiness_report(snapshot: dict, database: dict) -> dict:
    """Project a dashboard snapshot into a stable, secret-free report."""

    soak = snapshot["soak"]
    counts = snapshot["counts"]
    parent = snapshot.get("portfolio") or {}
    scheduler = [
        _fields(
            item,
            (
                "job_name",
                "scheduled_for",
                "status",
                "started_at",
                "finished_at",
                "error_code",
            ),
        )
        for item in snapshot["scheduler"]
    ]
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": snapshot["generated_at"],
        "status": _fields(snapshot["status"], ("code", "label", "reasons")),
        "readiness": {
            "evidence_version": soak["evidence_version"],
            "started_at": soak["started_at"],
            "target_at": soak["target_at"],
            "elapsed_seconds": soak["elapsed_seconds"],
            "remaining_seconds": soak["remaining_seconds"],
            "progress_pct": soak["progress_pct"],
            "preview_evaluation": _evaluation(soak["preview"]),
            "persisted_evaluation": _evaluation(soak.get("persisted_evaluation")),
        },
        "scopes": snapshot["scopes"],
        "providers": _providers(snapshot["providers"]),
        "scheduler": scheduler,
        "model_cost": {"daily_usd": snapshot["daily_model_cost_usd"]},
        "safety": {
            "paper_active": bool(parent.get("paper_active", False)),
            "entries_paused": bool(parent.get("entries_paused", True)),
            "halt_reason": parent.get("halt_reason"),
            "signal_count": counts["signals"],
            "fill_count": counts["fills"],
            "trade_count": counts["trades"],
        },
        "database": dict(database),
        "recommended_action": _recommended_action(snapshot, database),
    }


def collect_database_metrics(database: str | Path) -> dict[str, int | str]:
    """Inspect the live SQLite file without creating or modifying it."""

    path = Path(database).expanduser().resolve()
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise PermissionError(f"database must be a regular file: {path}")
    uri = f"{path.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        integrity_rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    usage = shutil.disk_usage(path.parent)
    wal_path = Path(f"{path}-wal")
    return {
        "integrity": "ok" if integrity_rows == ["ok"] else "failed",
        "size_bytes": details.st_size,
        "wal_size_bytes": wal_path.stat().st_size if wal_path.is_file() else 0,
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
    }


def serialize_report(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def prepare_report_output(destination: str | Path) -> Path:
    """Validate a not-yet-created report path and prepare its parent."""

    path = Path(destination).expanduser()
    path = path if path.is_absolute() else Path.cwd() / path
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"report output already exists: {path}")
    parent = path.parent
    parent_was_missing = not parent.exists()
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_details = parent.lstat()
    if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(parent_details.st_mode):
        raise PermissionError(f"report output parent must be a directory: {parent}")
    if parent_was_missing:
        parent.chmod(0o700)
    return path


def write_report(
    payload: dict,
    destination: str | Path,
    *,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> Path:
    """Atomically publish a private JSON report without replacing a file."""

    if owner_uid is not None and owner_uid < 0:
        raise ValueError("report owner uid must not be negative")
    if owner_gid is not None and owner_gid < 0:
        raise ValueError("report owner gid must not be negative")
    path = prepare_report_output(destination)
    parent = path.parent

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".soak-report.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialize_report(payload))
            handle.flush()
            os.fsync(handle.fileno())
        if owner_uid is not None or owner_gid is not None:
            os.chown(
                temporary,
                owner_uid if owner_uid is not None else -1,
                owner_gid if owner_gid is not None else -1,
            )
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise FileExistsError(f"report output already exists: {path}") from error
        temporary.unlink()
        directory_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return path
