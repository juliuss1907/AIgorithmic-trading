"""Global Docker deployment registry for the ``aigt`` command."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import tomllib
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from intraday.config import APP_DIRECTORY


SCHEMA_VERSION = 1
SAFE_ENVIRONMENT = {
    "INTRADAY_MODE": "paper",
    "PORTFOLIO_WORKER_MODE": "soak",
    "INTRADAY_OPERATOR_ACTIONS_ENABLED": "false",
    "INTRADAY_CROSS_VENUE_MODE": "shadow",
}


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


def admin_command(deployment: Deployment, arguments: list[str]) -> list[str]:
    return compose_command(
        deployment,
        "--profile",
        "admin",
        "run",
        "--rm",
        "admin",
        *arguments,
    )


def service_command(
    deployment: Deployment,
    action: str,
    *,
    tail: int = 200,
    follow: bool = True,
) -> list[str]:
    arguments = {
        "start": ["up", "-d", "worker", "web"],
        "stop": ["stop", "worker", "web"],
        "restart": ["restart", "worker", "web"],
        "logs": [
            "logs",
            "--tail",
            str(tail),
            *(["--follow"] if follow else []),
            "worker",
            "web",
        ],
    }.get(action)
    if arguments is None:
        raise ValueError(f"unsupported deployment action: {action}")
    return compose_command(deployment, *arguments)


def execute(command: list[str], *, cwd: Path) -> int:
    """Execute one deployment command with the caller's terminal attached."""
    return subprocess.run(command, cwd=cwd, check=False).returncode


def _env_values(payload: str) -> dict[str, str]:
    values = {}
    for line in payload.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        values[name] = value
    return values


def _replace_env_values(payload: str, replacements: dict[str, str]) -> str:
    remaining = dict(replacements)
    lines = []
    for line in payload.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0]
            if name in remaining:
                lines.append(f"{name}={remaining.pop(name)}")
                continue
        lines.append(line)
    lines.extend(f"{name}={value}" for name, value in remaining.items())
    return "\n".join(lines) + "\n"


def _atomic_private_write(path: Path, payload: str) -> None:
    if path.is_symlink():
        raise PermissionError(f"refusing to overwrite symlink: {path}")
    if path.exists() and not path.is_file():
        raise PermissionError(f"private target must be a regular file: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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


def _prepare_environment(
    project_root: Path, *, token_factory: Callable[[], str]
) -> tuple[Deployment, str]:
    example = project_root / ".env.example"
    env_file = project_root / ".env.intraday"
    if example.is_symlink() or not example.is_file():
        raise ValueError(f"environment template is missing: {example}")
    if env_file.is_symlink() or (env_file.exists() and not env_file.is_file()):
        raise PermissionError("environment file must be regular and must not be a symlink")
    payload = (
        env_file.read_text(encoding="utf-8")
        if env_file.exists()
        else example.read_text(encoding="utf-8")
    )
    values = _env_values(payload)
    for name, expected in SAFE_ENVIRONMENT.items():
        actual = values.get(name, expected)
        if actual != expected:
            if name == "INTRADAY_OPERATOR_ACTIONS_ENABLED":
                raise ValueError("operator actions must remain disabled during bootstrap")
            raise ValueError(f"{name} must remain {expected} during bootstrap")
    action_token = values.get("INTRADAY_OPERATOR_ACTION_TOKEN", "")
    if action_token and not action_token.startswith("replace-"):
        raise ValueError("operator action token must remain empty during bootstrap")
    control_token = values.get("INTRADAY_CONTROL_TOKEN", "")
    if not control_token or control_token.startswith("replace-"):
        control_token = token_factory()
    read_token = values.get("INTRADAY_OPERATOR_READ_TOKEN", "")
    if not read_token or read_token.startswith("replace-"):
        read_token = token_factory()
    if not control_token or not read_token or control_token == read_token:
        raise ValueError("bootstrap tokens must be non-empty and distinct")
    replacements = {
        **SAFE_ENVIRONMENT,
        "INTRADAY_CONTROL_TOKEN": control_token,
        "INTRADAY_OPERATOR_READ_TOKEN": read_token,
        "INTRADAY_OPERATOR_ACTION_TOKEN": "",
    }
    rendered = _replace_env_values(payload, replacements)
    if not env_file.exists() or env_file.read_text(encoding="utf-8") != rendered:
        _atomic_private_write(env_file, rendered)
    else:
        env_file.chmod(0o600)

    secret_directory = project_root / "state" / "provider-secrets"
    if secret_directory.is_symlink() or (
        secret_directory.exists() and not secret_directory.is_dir()
    ):
        raise PermissionError("provider secret directory must be a real directory")
    secret_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret_directory.chmod(0o700)
    secret_file = secret_directory / "provider-secrets.toml"
    if secret_file.exists():
        details = secret_file.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise PermissionError("provider secret path must be a regular file")
        secret_file.chmod(0o600)
    else:
        descriptor = os.open(secret_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    return Deployment.for_project(project_root), read_token


def bootstrap(
    project_root: Path,
    *,
    with_hermes: bool = False,
    runner: Callable[..., int] | None = None,
    token_factory: Callable[[], str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> dict:
    root = Path(project_root).expanduser().resolve()
    run = runner or execute
    make_token = token_factory or (lambda: secrets.token_urlsafe(32))
    deployment, read_token = _prepare_environment(root, token_factory=make_token)
    steps = (
        ("Compose validation", compose_command(deployment, "config", "--quiet")),
        (
            "AIGT startup",
            compose_command(
                deployment, "up", "--build", "-d", "--wait", "worker", "web"
            ),
        ),
        ("AIGT doctor", admin_command(deployment, ["doctor"])),
    )
    for label, command in steps:
        try:
            code = run(command, cwd=root)
        except FileNotFoundError as error:
            raise RuntimeError("Docker and Docker Compose v2 are required") from error
        if code:
            raise RuntimeError(f"{label} failed with exit code {code}")
    save_deployment(deployment)
    hermes_status = "not-requested"
    if with_hermes:
        locate = which or shutil.which
        if locate("hermes") is None:
            hermes_status = "skipped-not-installed"
        else:
            hermes_status = _install_hermes_profile(
                deployment,
                read_token=read_token,
                runner=run,
            )
    return {
        "status": "ok",
        "deployment": "docker-compose",
        "project_root": str(root),
        "worker_mode": "soak",
        "dashboard": "http://127.0.0.1:8081",
        "hermes": hermes_status,
        "actions_enabled": False,
    }


def _install_hermes_profile(
    deployment: Deployment,
    *,
    read_token: str,
    runner: Callable[..., int],
) -> str:
    source = deployment.project_root / "integrations" / "hermes" / "trading-ops"
    if not (source / "distribution.yaml").is_file():
        raise RuntimeError(f"Hermes trading distribution is missing: {source}")
    configured_home = os.getenv("HERMES_HOME")
    hermes_home = (
        Path(configured_home).expanduser()
        if configured_home
        else Path.home() / ".hermes"
    )
    profile = hermes_home / "profiles" / "trading-ops"
    if profile.is_symlink():
        raise PermissionError("Hermes trading-ops profile must not be a symlink")
    existing = profile.is_dir()
    command = (
        ["hermes", "profile", "update", "trading-ops", "--yes"]
        if existing
        else [
            "hermes",
            "profile",
            "install",
            str(source),
            "--name",
            "trading-ops",
            "--alias",
            "--yes",
        ]
    )
    code = runner(command, cwd=deployment.project_root)
    if code:
        raise RuntimeError(f"Hermes profile installation failed with exit code {code}")
    if not profile.is_dir():
        raise RuntimeError("Hermes did not create the trading-ops profile")
    env_file = profile / ".env"
    payload = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    rendered = _replace_env_values(
        payload,
        {
            "AIGT_OPERATOR_URL": "http://127.0.0.1:8081",
            "AIGT_OPERATOR_READ_TOKEN": read_token,
            "AIGT_OPERATOR_ACTION_TOKEN": "",
        },
    )
    _atomic_private_write(env_file, rendered)
    doctor = [
        "hermes",
        "-p",
        "trading-ops",
        "plugins",
        "doctor",
        "aigt_operator",
        "--ci",
    ]
    code = runner(doctor, cwd=deployment.project_root)
    if code:
        raise RuntimeError(f"Hermes plugin doctor failed with exit code {code}")
    return "updated-read-only" if existing else "installed-read-only"
