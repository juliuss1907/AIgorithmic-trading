"""Immutable trade-journal export for future Kev fine-tuning."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from intraday.config import resolve_database_path


DIRECTION_QUESTION = {
    "type": "choice",
    "instructions": "What should I do with this position?",
    "criteria": {
        "strong_buy": "Enter or add aggressively to a high-conviction long position.",
        "buy": "Enter or add to a long position.",
        "hold": "Do not open a new position or keep the current position unchanged.",
        "take_profit": "Reduce or close a profitable existing position.",
        "sell": "Enter or add to a short position, or exit a long position.",
        "strong_sell": "Enter or add aggressively to a high-conviction short position.",
    },
}


def _direction_label(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def export_training_data(
    min_pnl_pct: float = 0.5,
    max_pnl_pct: float = -0.5,
    *,
    database: str | Path | None = None,
    output_dir: str | Path = "training_data",
    now: datetime | None = None,
) -> list[dict]:
    """Export unambiguous completed paper trades as deterministic Kev JSONL."""
    if not max_pnl_pct < 0 < min_pnl_pct:
        raise ValueError("thresholds must satisfy max_pnl_pct < 0 < min_pnl_pct")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("export timestamp must be timezone-aware")
    path = resolve_database_path(database)
    rows: list[dict] = []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        records = connection.execute(
            "SELECT s.state_snapshot, t.direction, t.pnl_pct "
            "FROM signals s JOIN trades t ON t.signal_id=s.id "
            "WHERE s.gate_passed=1 AND t.pnl_pct IS NOT NULL ORDER BY t.id"
        ).fetchall()
    for record in records:
        pnl_pct = float(record["pnl_pct"])
        if pnl_pct > min_pnl_pct:
            answer = _direction_label(record["direction"])
        elif pnl_pct < max_pnl_pct:
            answer = "hold"
        else:
            continue
        rows.append(
            {
                "state": record["state_snapshot"],
                "questions": {"direction": DIRECTION_QUESTION},
                "answer": {"direction": answer},
            }
        )

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"kev_finetune_{now.astimezone(timezone.utc):%Y%m%d}.jsonl"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
                handle.write("\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return rows
