"""Global Docker deployment registry for the ``aigt`` command."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from intraday.config import APP_DIRECTORY


SCHEMA_VERSION = 1


def deployment_registry_path() -> Path:
    configured = os.getenv("XDG_CONFIG_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".config"
    return root / APP_DIRECTORY / "deployment.toml"


@dataclass(frozen=True)
class Deployment:
    project_root: Path
    compose_file: Path
    env_file: Path

    @classmethod
    def for_project(cls, project_root: Path) -> "Deployment":
        root = Path(project_root).expanduser()
        if not root.is_absolute():
            raise ValueError("deployment project root must be absolute")
        root = root.resolve()
        compose_file = root / "deploy" / "intraday" / "compose.yaml"
        env_file = root / ".env.intraday"
        if not compose_file.is_file():
            raise ValueError(f"deployment compose file is missing: {compose_file}")
        if not env_file.is_file():
            raise ValueError(f"deployment env file is missing: {env_file}")
        return cls(root, compose_file, env_file)


def _validate_registry_file(path: Path) -> None:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise PermissionError("deployment registry must be a regular file")
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise PermissionError("deployment registry permissions must be 0600")


def save_deployment(deployment: Deployment) -> Path:
    path = deployment_registry_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = "\n".join(
        (
            f"schema_version = {SCHEMA_VERSION}",
            'kind = "docker-compose"',
            f"project_root = {json.dumps(str(deployment.project_root))}",
            f"compose_file = {json.dumps(str(deployment.compose_file))}",
            f"env_file = {json.dumps(str(deployment.env_file))}",
            "",
        )
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix=".deployment.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return path


def load_deployment() -> Deployment | None:
    path = deployment_registry_path()
    if not path.exists():
        return None
    _validate_registry_file(path)
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported deployment registry schema")
    if payload.get("kind") != "docker-compose":
        raise ValueError("unsupported deployment kind")
    deployment = Deployment(
        project_root=Path(payload["project_root"]),
        compose_file=Path(payload["compose_file"]),
        env_file=Path(payload["env_file"]),
    )
    expected = Deployment.for_project(deployment.project_root)
    if deployment != expected:
        raise ValueError("deployment registry paths do not match the project")
    return deployment


def compose_command(deployment: Deployment, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(deployment.env_file),
        "-f",
        str(deployment.compose_file),
        *arguments,
    ]

