import stat
from pathlib import Path

import pytest

from intraday.deployment import (
    Deployment,
    compose_command,
    load_deployment,
    save_deployment,
)


def project(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "deploy" / "intraday").mkdir(parents=True)
    (root / "deploy" / "intraday" / "compose.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (root / ".env.intraday").write_text("INTRADAY_MODE=paper\n", encoding="utf-8")
    return root


def test_deployment_registry_is_global_and_private(monkeypatch, tmp_path):
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    root = project(tmp_path)

    saved = save_deployment(Deployment.for_project(root))
    monkeypatch.chdir(tmp_path)

    assert load_deployment() == Deployment.for_project(root)
    assert saved == config_home / "aigorithmic-trading" / "deployment.toml"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700


def test_deployment_rejects_missing_or_relative_project_paths(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        Deployment.for_project(Path("relative"))

    with pytest.raises(ValueError, match="compose file"):
        Deployment.for_project(tmp_path / "missing")


def test_compose_command_uses_registered_absolute_files(tmp_path):
    deployment = Deployment.for_project(project(tmp_path))

    assert compose_command(deployment, "config", "--quiet") == [
        "docker",
        "compose",
        "--env-file",
        str(deployment.env_file),
        "-f",
        str(deployment.compose_file),
        "config",
        "--quiet",
    ]

