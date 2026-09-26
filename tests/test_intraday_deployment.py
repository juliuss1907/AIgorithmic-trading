import stat
from pathlib import Path

import pytest

from intraday.deployment import (
    Deployment,
    admin_command,
    backup_command,
    bootstrap,
    compose_command,
    load_deployment,
    save_deployment,
    service_command,
    soak_report_command,
)


def project(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "deploy" / "intraday").mkdir(parents=True)
    (root / "deploy" / "intraday" / "compose.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (root / ".env.intraday").write_text("INTRADAY_MODE=paper\n", encoding="utf-8")
    return root


def bootstrap_project(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "deploy" / "intraday").mkdir(parents=True)
    (root / "deploy" / "intraday" / "compose.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (root / ".env.example").write_text(
        "\n".join(
            (
                "INTRADAY_MODE=paper",
                "PORTFOLIO_WORKER_MODE=soak",
                "INTRADAY_CONTROL_TOKEN=replace-control",
                "INTRADAY_OPERATOR_READ_TOKEN=replace-read",
                "INTRADAY_OPERATOR_ACTION_TOKEN=replace-action",
                "INTRADAY_OPERATOR_ACTIONS_ENABLED=false",
                "INTRADAY_CROSS_VENUE_MODE=shadow",
                "",
            )
        ),
        encoding="utf-8",
    )
    distribution = root / "integrations" / "hermes" / "trading-ops"
    distribution.mkdir(parents=True)
    (distribution / "distribution.yaml").write_text(
        "name: trading-ops\nversion: 0.1.0\n", encoding="utf-8"
    )
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


def test_admin_command_preserves_aigt_arguments(tmp_path):
    deployment = Deployment.for_project(project(tmp_path))

    assert admin_command(deployment, ["provider", "setup"]) == [
        *compose_command(deployment),
        "--profile",
        "admin",
        "run",
        "--rm",
        "admin",
        "provider",
        "setup",
    ]


def test_backup_command_mounts_host_output_without_starting_dependencies(tmp_path):
    deployment = Deployment.for_project(project(tmp_path))
    output_dir = tmp_path / "private-backups"

    assert backup_command(
        deployment, output_dir, owner_uid=1234, owner_gid=5678
    ) == [
        *compose_command(deployment),
        "--profile",
        "admin",
        "run",
        "--rm",
        "--no-deps",
        "--volume",
        f"{output_dir}:{output_dir}",
        "admin",
        "backup",
        "create",
        "--database",
        "/app/state/intraday/intraday.sqlite3",
        "--output-dir",
        str(output_dir),
        "--owner-uid",
        "1234",
        "--owner-gid",
        "5678",
    ]


def test_soak_report_command_runs_isolated_admin_without_output_mount(tmp_path):
    deployment = Deployment.for_project(project(tmp_path))

    assert soak_report_command(deployment) == [
        *compose_command(deployment),
        "--profile",
        "admin",
        "run",
        "--rm",
        "--no-deps",
        "admin",
        "portfolio",
        "soak",
        "report",
        "--database",
        "/app/state/intraday/intraday.sqlite3",
    ]


def test_soak_report_command_mounts_only_output_parent_and_restores_owner(tmp_path):
    deployment = Deployment.for_project(project(tmp_path))
    output = tmp_path / "reports" / "readiness.json"

    assert soak_report_command(
        deployment, output=output, owner_uid=1234, owner_gid=5678
    ) == [
        *compose_command(deployment),
        "--profile",
        "admin",
        "run",
        "--rm",
        "--no-deps",
        "--volume",
        f"{output.parent}:{output.parent}",
        "admin",
        "portfolio",
        "soak",
        "report",
        "--database",
        "/app/state/intraday/intraday.sqlite3",
        "--output",
        str(output),
        "--owner-uid",
        "1234",
        "--owner-gid",
        "5678",
    ]


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("start", ["up", "-d", "worker", "web"]),
        ("stop", ["stop", "worker", "web"]),
        ("restart", ["restart", "worker", "web"]),
        ("logs", ["logs", "--tail", "50", "--follow", "worker", "web"]),
    ],
)
def test_service_commands_are_bounded_to_aigt_services(tmp_path, action, expected):
    deployment = Deployment.for_project(project(tmp_path))

    assert service_command(deployment, action, tail=50, follow=True) == [
        *compose_command(deployment),
        *expected,
    ]


def test_service_command_rejects_unknown_actions(tmp_path):
    deployment = Deployment.for_project(project(tmp_path))

    with pytest.raises(ValueError, match="unsupported deployment action"):
        service_command(deployment, "delete")


def test_bootstrap_creates_safe_files_starts_core_and_registers_globally(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = bootstrap_project(tmp_path)
    calls = []
    tokens = iter(("control-secret", "read-secret"))

    result = bootstrap(
        root,
        runner=lambda command, *, cwd: calls.append((command, cwd)) or 0,
        token_factory=lambda: next(tokens),
    )

    deployment = Deployment.for_project(root)
    assert result == {
        "status": "ok",
        "deployment": "docker-compose",
        "project_root": str(root),
        "worker_mode": "soak",
        "dashboard": "http://127.0.0.1:8081",
        "hermes": "not-requested",
        "actions_enabled": False,
    }
    assert calls == [
        (compose_command(deployment, "config", "--quiet"), root),
        (
            compose_command(
                deployment,
                "--profile",
                "admin",
                "build",
                "worker",
                "web",
                "admin",
            ),
            root,
        ),
        (
            compose_command(
                deployment,
                "up",
                "-d",
                "--wait",
                "worker",
                "web",
            ),
            root,
        ),
        (admin_command(deployment, ["doctor"]), root),
    ]
    env = (root / ".env.intraday").read_text(encoding="utf-8")
    assert "INTRADAY_CONTROL_TOKEN=control-secret" in env
    assert "INTRADAY_OPERATOR_READ_TOKEN=read-secret" in env
    assert "INTRADAY_OPERATOR_ACTION_TOKEN=" in env
    assert "INTRADAY_OPERATOR_ACTIONS_ENABLED=false" in env
    assert stat.S_IMODE((root / ".env.intraday").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "state" / "provider-secrets").stat().st_mode) == 0o700
    secret_file = root / "state" / "provider-secrets" / "provider-secrets.toml"
    assert secret_file.read_text(encoding="utf-8") == ""
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert load_deployment() == deployment


def test_bootstrap_is_idempotent_and_never_rotates_existing_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = bootstrap_project(tmp_path)
    first_tokens = iter(("first-control", "first-read"))
    bootstrap(
        root,
        runner=lambda *_args, **_kwargs: 0,
        token_factory=lambda: next(first_tokens),
    )
    original = (root / ".env.intraday").read_bytes()

    bootstrap(root, runner=lambda *_args, **_kwargs: 0, token_factory=lambda: "second")

    assert (root / ".env.intraday").read_bytes() == original


def test_bootstrap_refuses_unsafe_existing_action_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = bootstrap_project(tmp_path)
    (root / ".env.intraday").write_text(
        "\n".join(
            (
                "INTRADAY_MODE=paper",
                "PORTFOLIO_WORKER_MODE=soak",
                "INTRADAY_CONTROL_TOKEN=control",
                "INTRADAY_OPERATOR_READ_TOKEN=read",
                "INTRADAY_OPERATOR_ACTION_TOKEN=action",
                "INTRADAY_OPERATOR_ACTIONS_ENABLED=true",
                "INTRADAY_CROSS_VENUE_MODE=shadow",
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="actions must remain disabled"):
        bootstrap(root, runner=lambda *_args, **_kwargs: pytest.fail("must not run"))


def test_bootstrap_refuses_symlinked_environment_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = bootstrap_project(tmp_path)
    victim = tmp_path / "victim.env"
    victim.write_text("DO_NOT_OVERWRITE=true\n", encoding="utf-8")
    (root / ".env.intraday").symlink_to(victim)

    with pytest.raises(PermissionError, match="environment file must be regular"):
        bootstrap(root, runner=lambda *_args, **_kwargs: pytest.fail("must not run"))

    assert victim.read_text(encoding="utf-8") == "DO_NOT_OVERWRITE=true\n"


def test_bootstrap_refuses_symlinked_provider_secret_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = bootstrap_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "state").mkdir()
    (root / "state" / "provider-secrets").symlink_to(outside, target_is_directory=True)
    tokens = iter(("control", "read"))

    with pytest.raises(PermissionError, match="secret directory must be a real directory"):
        bootstrap(
            root,
            runner=lambda *_args, **_kwargs: pytest.fail("must not run"),
            token_factory=lambda: next(tokens),
        )


def test_bootstrap_does_not_register_failed_compose_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = bootstrap_project(tmp_path)
    tokens = iter(("control", "read"))

    with pytest.raises(RuntimeError, match="Compose validation failed"):
        bootstrap(
            root,
            runner=lambda *_args, **_kwargs: 9,
            token_factory=lambda: next(tokens),
        )

    assert load_deployment() is None


def test_bootstrap_treats_missing_hermes_as_an_optional_addon(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = bootstrap_project(tmp_path)
    tokens = iter(("control", "read"))

    result = bootstrap(
        root,
        with_hermes=True,
        runner=lambda *_args, **_kwargs: 0,
        token_factory=lambda: next(tokens),
        which=lambda _name: None,
    )

    assert result["status"] == "ok"
    assert result["hermes"] == "skipped-not-installed"


def test_bootstrap_installs_read_only_hermes_profile_when_requested(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    hermes_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    root = bootstrap_project(tmp_path)
    tokens = iter(("control", "read"))
    calls = []

    def runner(command, *, cwd):
        calls.append((command, cwd))
        if command[:3] == ["hermes", "profile", "install"]:
            (hermes_home / "profiles" / "trading-ops").mkdir(parents=True)
        return 0

    result = bootstrap(
        root,
        with_hermes=True,
        runner=runner,
        token_factory=lambda: next(tokens),
        which=lambda name: "/usr/bin/hermes" if name == "hermes" else None,
    )

    source = root / "integrations" / "hermes" / "trading-ops"
    assert result["hermes"] == "installed-read-only"
    assert calls[-2:] == [
        (
            [
                "hermes", "profile", "install", str(source),
                "--name", "trading-ops", "--alias", "--yes",
            ],
            root,
        ),
        (
            [
                "hermes", "-p", "trading-ops", "plugins", "doctor",
                "aigt_operator", "--ci",
            ],
            root,
        ),
    ]
    profile_env = hermes_home / "profiles" / "trading-ops" / ".env"
    payload = profile_env.read_text(encoding="utf-8")
    assert "AIGT_OPERATOR_URL=http://127.0.0.1:8081" in payload
    assert "AIGT_OPERATOR_READ_TOKEN=read" in payload
    assert "AIGT_OPERATOR_ACTION_TOKEN=" in payload
    assert stat.S_IMODE(profile_env.stat().st_mode) == 0o600
