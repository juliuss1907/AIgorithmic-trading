"""No-agent cron watcher: empty stdout means no Telegram delivery."""

from __future__ import annotations

import os
from pathlib import Path

from _aigt_http import OperatorReadClient


def _read_cursor(path: Path) -> int:
    try:
        value = int(path.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return 0
    return max(0, value)


def _write_cursor(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(f"{value}\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def _write_state(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(f"{value}\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def collect_alerts(client, cursor_path: Path) -> str:
    payload = client.alerts(_read_cursor(cursor_path))
    alerts = payload.get("alerts")
    next_cursor = payload.get("next_cursor")
    if not isinstance(alerts, list) or not isinstance(next_cursor, int):
        raise RuntimeError("operator_invalid_alert_response")
    _write_cursor(cursor_path, next_cursor)
    lines = []
    for alert in alerts:
        lines.append(
            "[{severity}] {kind}: {message} ({created_at})".format(
                severity=str(alert.get("severity", "warning")).upper(),
                kind=alert.get("kind", "unknown"),
                message=alert.get("message", "No message"),
                created_at=alert.get("created_at", "unknown time"),
            )
        )
    return "\n".join(lines)


def watch_once(client_factory, cursor_path: Path, health_path: Path) -> str:
    try:
        output = collect_alerts(client_factory(), cursor_path)
    except (RuntimeError, ValueError) as error:
        state = f"error:{error}"
        previous = health_path.read_text().strip() if health_path.exists() else ""
        _write_state(health_path, state)
        return "" if previous == state else f"[CRITICAL] operator_watcher_error: {error}"
    previous = health_path.read_text().strip() if health_path.exists() else ""
    _write_state(health_path, "ok")
    if previous.startswith("error:"):
        recovery = "[WARNING] operator_watcher_recovered: Operator API is reachable again."
        return f"{recovery}\n{output}" if output else recovery
    return output


def main() -> int:
    hermes_home = Path(os.getenv("HERMES_HOME") or Path.home() / ".hermes")
    cursor = hermes_home / "local" / "aigt-alert-cursor"
    health = hermes_home / "local" / "aigt-alert-health"
    output = watch_once(OperatorReadClient, cursor, health)
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
