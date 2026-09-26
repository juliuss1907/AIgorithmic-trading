import json
import os
import sqlite3
import stat
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from intraday.__main__ import main
from intraday.config import IntradayConfig
from intraday.contracts import Direction, FeatureSnapshot
from intraday.runtime import run_once
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def snapshot(at=NOW):
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=at,
        built_at=at,
        bid=99_990,
        ask=100_010,
        features={"price": 100_000, "mark_price": 100_000},
        freshness={"candles": True, "order_book": True},
    )


def test_project_exports_aigt_console_script():
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)

    assert project["project"]["scripts"]["aigt"] == "intraday.__main__:main"
    assert project["build-system"]["build-backend"] == "uv_build"
    assert project["tool"]["uv"]["build-backend"]["module-name"] == [
        "intraday",
        "lab",
    ]


def test_bare_aigt_prints_help_and_exits_successfully(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["aigt"])

    main()

    output = capsys.readouterr().out
    assert output.startswith("usage: aigt")
    assert "connect" in output
    assert "provider" in output
    assert "migrate-state" in output
    assert "journal" in output
    assert "start" in output
    assert "stop" in output
    assert "restart" in output
    assert "logs" in output
    assert "setup" in output
    assert "backup" in output


def test_backup_create_and_verify_cli_with_explicit_database(
    monkeypatch, capsys, tmp_path
):
    source = tmp_path / "intraday.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('paper-state')")
    output_dir = tmp_path / "backups"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aigt",
            "backup",
            "create",
            "--database",
            str(source),
            "--output-dir",
            str(output_dir),
        ],
    )

    main()

    created = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        sys, "argv", ["aigt", "backup", "verify", created["backup"]]
    )
    main()
    verified = json.loads(capsys.readouterr().out)
    assert created["status"] == "created"
    assert verified == {**created, "status": "ok"}


def test_global_backup_create_routes_only_to_admin_container(
    monkeypatch, tmp_path
):
    from intraday import deployment as deployment_module

    root = tmp_path / "checkout"
    (root / "deploy" / "intraday").mkdir(parents=True)
    (root / "deploy" / "intraday" / "compose.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (root / ".env.intraday").write_text("INTRADAY_MODE=paper\n", encoding="utf-8")
    registered = deployment_module.Deployment.for_project(root)
    output_dir = tmp_path / "host-backups"
    calls = []
    monkeypatch.setattr(deployment_module, "load_deployment", lambda: registered)
    monkeypatch.setattr(
        deployment_module,
        "execute",
        lambda command, *, cwd: calls.append((command, cwd)) or 0,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["aigt", "backup", "create", "--output-dir", str(output_dir)],
    )

    main()

    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert calls == [
        (
            deployment_module.backup_command(
                registered,
                output_dir,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
            ),
            root,
        )
    ]


def test_soak_report_cli_prints_and_optionally_writes_identical_read_only_json(
    monkeypatch, capsys, tmp_path
):
    database = tmp_path / "intraday.sqlite3"
    store = IntradayStore(database)
    output = tmp_path / "reports" / "readiness.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aigt",
            "portfolio",
            "soak",
            "report",
            "--database",
            str(database),
            "--output",
            str(output),
        ],
    )

    main()

    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert output.read_text(encoding="utf-8") == stdout
    assert report["status"]["code"] == "WAITING"
    assert report["recommended_action"] == "wait"
    assert report["database"]["integrity"] == "ok"
    assert store.latest_portfolio_soak_evaluation() is None


def test_soak_report_refuses_missing_database_without_creating_it(
    monkeypatch, tmp_path
):
    database = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aigt",
            "portfolio",
            "soak",
            "report",
            "--database",
            str(database),
        ],
    )

    with pytest.raises(SystemExit, match="database does not exist"):
        main()

    assert not database.exists()


@pytest.mark.parametrize("with_output", [False, True])
def test_global_soak_report_routes_to_isolated_admin_container(
    monkeypatch, tmp_path, with_output
):
    from intraday import deployment as deployment_module

    root = tmp_path / "checkout"
    (root / "deploy" / "intraday").mkdir(parents=True)
    (root / "deploy" / "intraday" / "compose.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (root / ".env.intraday").write_text(
        "INTRADAY_MODE=paper\n", encoding="utf-8"
    )
    registered = deployment_module.Deployment.for_project(root)
    output = tmp_path / "reports" / "readiness.json" if with_output else None
    argv = ["aigt", "portfolio", "soak", "report"]
    if output is not None:
        argv.extend(("--output", str(output)))
    calls = []
    monkeypatch.setattr(deployment_module, "load_deployment", lambda: registered)
    monkeypatch.setattr(
        deployment_module,
        "execute",
        lambda command, *, cwd: calls.append((command, cwd)) or 0,
    )
    monkeypatch.setattr(sys, "argv", argv)

    main()

    assert calls == [
        (
            deployment_module.soak_report_command(
                registered,
                output=output,
                owner_uid=os.getuid() if output is not None else None,
                owner_gid=os.getgid() if output is not None else None,
            ),
            root,
        )
    ]
    if output is not None:
        assert output.parent.is_dir()


def test_provider_setup_command_has_been_removed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["aigt", "provider", "setup"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_global_aigt_routes_connect_to_registered_docker_deployment(
    monkeypatch, tmp_path
):
    from intraday import deployment as deployment_module

    root = tmp_path / "checkout"
    (root / "deploy" / "intraday").mkdir(parents=True)
    (root / "deploy" / "intraday" / "compose.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (root / ".env.intraday").write_text("INTRADAY_MODE=paper\n", encoding="utf-8")
    registered = deployment_module.Deployment.for_project(root)
    calls = []
    monkeypatch.setattr(deployment_module, "load_deployment", lambda: registered)
    monkeypatch.setattr(
        deployment_module,
        "execute",
        lambda command, *, cwd: calls.append((command, cwd)) or 0,
    )
    monkeypatch.setattr(sys, "argv", ["aigt", "connect", "jev", "openrouter"])

    main()

    assert calls == [
        (
            deployment_module.admin_command(
                registered, ["connect", "jev", "openrouter"]
            ),
            root,
        )
    ]


def test_global_aigt_routes_doctor_to_registered_docker_deployment(
    monkeypatch, tmp_path
):
    from intraday import deployment as deployment_module

    root = tmp_path / "checkout"
    (root / "deploy" / "intraday").mkdir(parents=True)
    (root / "deploy" / "intraday" / "compose.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (root / ".env.intraday").write_text("INTRADAY_MODE=paper\n", encoding="utf-8")
    registered = deployment_module.Deployment.for_project(root)
    calls = []
    monkeypatch.setattr(deployment_module, "load_deployment", lambda: registered)
    monkeypatch.setattr(
        deployment_module,
        "execute",
        lambda command, *, cwd: calls.append((command, cwd)) or 0,
    )
    monkeypatch.setattr(sys, "argv", ["aigt", "doctor"])

    main()

    assert calls == [
        (deployment_module.admin_command(registered, ["doctor"]), root)
    ]


def test_global_aigt_routes_analysis_to_registered_docker_deployment(
    monkeypatch, tmp_path
):
    from intraday import deployment as deployment_module

    root = tmp_path / "checkout"
    (root / "deploy" / "intraday").mkdir(parents=True)
    (root / "deploy" / "intraday" / "compose.yaml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (root / ".env.intraday").write_text("INTRADAY_MODE=paper\n", encoding="utf-8")
    registered = deployment_module.Deployment.for_project(root)
    calls = []
    monkeypatch.setattr(deployment_module, "load_deployment", lambda: registered)
    monkeypatch.setattr(
        deployment_module,
        "execute",
        lambda command, *, cwd: calls.append((command, cwd)) or 0,
    )
    monkeypatch.setattr(sys, "argv", ["aigt", "analysis"])

    main()

    assert calls == [
        (deployment_module.admin_command(registered, ["analysis"]), root)
    ]


def test_aigt_setup_bootstraps_and_registers_the_selected_project(
    monkeypatch, capsys, tmp_path
):
    from intraday import deployment as deployment_module

    calls = []
    expected = {"status": "ok", "project_root": str(tmp_path)}
    monkeypatch.setattr(
        deployment_module,
        "bootstrap",
        lambda root, *, with_hermes: calls.append((root, with_hermes)) or expected,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["aigt", "setup", "--project-root", str(tmp_path), "--with-hermes"],
    )

    main()

    assert calls == [(tmp_path, True)]
    assert json.loads(capsys.readouterr().out) == expected


def test_explicit_database_keeps_doctor_in_native_mode(monkeypatch, capsys, tmp_path):
    from intraday import deployment as deployment_module

    monkeypatch.setattr(
        deployment_module,
        "load_deployment",
        lambda: pytest.fail("explicit database must bypass deployment discovery"),
    )
    database = tmp_path / "native.sqlite"
    monkeypatch.setattr(
        sys, "argv", ["aigt", "doctor", "--database", str(database)]
    )

    main()

    assert json.loads(capsys.readouterr().out)["database"] == str(database)


def test_experiment_status_and_retrospective_cli_are_available(
    monkeypatch, capsys, tmp_path
):
    database = tmp_path / "intraday.sqlite"
    monkeypatch.setattr(
        sys, "argv",
        ["aigt", "portfolio", "experiment", "status", "--database", str(database)],
    )
    main()
    status = json.loads(capsys.readouterr().out)
    assert set(status) == {"spot_daily", "perp_intraday"}

    monkeypatch.setattr(
        sys, "argv",
        [
            "aigt", "portfolio", "retrospective", "run", "--once",
            "--date", "2026-09-22", "--database", str(database),
        ],
    )
    main()
    report = json.loads(capsys.readouterr().out)
    assert report["report_date"] == "2026-09-22"
    assert set(report["scopes"]) == {"spot_daily", "perp_intraday"}


def test_journal_export_cli_writes_json_summary_and_jsonl(
    monkeypatch, capsys, tmp_path
):
    database = tmp_path / "intraday.sqlite"
    IntradayStore(database)
    output_dir = tmp_path / "training_data"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aigt",
            "journal",
            "export",
            "--database",
            str(database),
            "--output-dir",
            str(output_dir),
        ],
    )

    main()

    result = json.loads(capsys.readouterr().out)
    assert result["exported"] == 0
    assert result["positive"] == 0
    assert result["hold"] == 0
    assert result["ambiguous_skipped"] == 0
    assert Path(result["output_file"]).exists()


def test_aigt_version_uses_project_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["aigt", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "aigt 0.1.0\n"


def test_default_paths_use_xdg_directories(monkeypatch, tmp_path):
    state_home = tmp_path / "state-home"
    config_home = tmp_path / "config-home"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("INTRADAY_DATABASE", raising=False)
    monkeypatch.delenv("INTRADAY_PROVIDER_SECRETS_FILE", raising=False)

    config = IntradayConfig.from_environment()

    assert config.database == state_home / "aigorithmic-trading" / "intraday.sqlite3"
    assert config.provider_secrets_file == (
        config_home / "aigorithmic-trading" / "provider-secrets.toml"
    )


def test_multi_cadence_defaults_are_safe_and_explicit():
    config = IntradayConfig()

    assert config.risk_interval_seconds == 5
    assert config.order_book_interval_seconds == 15
    assert config.perp_decision_interval_seconds == 30
    assert config.derivatives_interval_seconds == 60
    assert config.compact_shadow_interval_seconds == 900
    assert config.news_interval_seconds == 1800
    assert config.llm_analysis_interval_seconds == 3600
    assert config.retrospective_hour_vietnam == 9
    assert config.operator_actions_enabled is False
    assert config.operator_read_token is None
    assert config.operator_action_token is None


def test_operator_credentials_are_separate_and_actions_require_both(monkeypatch):
    monkeypatch.setenv("INTRADAY_OPERATOR_READ_TOKEN", "read-token")
    monkeypatch.setenv("INTRADAY_OPERATOR_ACTION_TOKEN", "action-token")
    monkeypatch.setenv("INTRADAY_OPERATOR_ACTIONS_ENABLED", "true")

    config = IntradayConfig.from_environment()

    assert config.operator_read_token == "read-token"
    assert config.operator_action_token == "action-token"
    assert config.operator_actions_enabled is True

    with pytest.raises(ValueError, match="must be different"):
        IntradayConfig(
            operator_read_token="shared-token",
            operator_action_token="shared-token",
            operator_actions_enabled=True,
        )


def test_explicit_database_precedes_environment_and_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    monkeypatch.setenv("INTRADAY_DATABASE", str(tmp_path / "environment.sqlite"))
    explicit = tmp_path / "explicit.sqlite"

    assert IntradayConfig.from_environment(database=explicit).database == explicit


def test_migrate_state_copies_valid_database_without_removing_source(
    monkeypatch, capsys, tmp_path
):
    source = tmp_path / "legacy" / "intraday.sqlite3"
    source.parent.mkdir()
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('legacy-paper-state')")
    destination = tmp_path / "new-state" / "intraday.sqlite3"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aigt",
            "migrate-state",
            "--from",
            str(source),
            "--database",
            str(destination),
        ],
    )

    main()

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "copied",
        "source": str(source.resolve()),
        "database": str(destination.resolve()),
        "integrity": "ok",
    }
    assert source.exists()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == (
            "legacy-paper-state"
        )


def test_migrate_state_refuses_to_overwrite_existing_database(
    monkeypatch, tmp_path
):
    source = tmp_path / "source.sqlite"
    destination = tmp_path / "destination.sqlite"
    for path, value in ((source, "source"), (destination, "destination")):
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker VALUES (?)", (value,))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aigt",
            "migrate-state",
            "--from",
            str(source),
            "--database",
            str(destination),
        ],
    )

    with pytest.raises(SystemExit, match="already exists"):
        main()

    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "destination"


def test_doctor_reports_safe_defaults(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["intraday", "doctor", "--database", str(tmp_path / "intraday.sqlite")],
    )

    main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert result["mode"] == "paper"
    assert result["provider"] == "stub"
    assert result["execution_enabled"] is False
    assert result["leverage"] == 3
    assert result["cross_venue_mode"] == "shadow"
    assert result["hyperliquid_enabled"] is True
    assert result["schema_version"] == 18
    assert result["feature_schema_version"] == "2"
    assert result["soak_evidence_version"] == "scope-price-v2"
    assert result["markets"] == {
        "perp_intraday": "Binance USD-M perpetual",
        "spot_daily": "Binance Spot",
    }
    assert result["cadences_seconds"] == {
        "risk": 5.0,
        "order_book": 15.0,
        "spot_quote": 15.0,
        "perp_numeric": 30.0,
        "derivatives": 60.0,
        "compact_shadow": 900.0,
        "llm_analysis": 3600,
    }
    assert result["retrospective"] == {
        "hour": 9,
        "timezone": "Asia/Ho_Chi_Minh",
    }
    assert result["scheduler"] == []


def test_one_stub_tick_is_persisted_without_exchange_execution(tmp_path):
    result = run_once(
        database=tmp_path / "intraday.sqlite",
        snapshot=snapshot(),
        direction=Direction.BUY,
        now=NOW,
    )

    assert result["direction"] == "Buy"
    assert result["gate"] == "authorized"
    assert result["paper_fill"] is True
    assert result["execution_enabled"] is False


def test_shadow_tick_reports_cross_venue_assessment_without_applying_it(tmp_path):
    original = snapshot()
    enriched = FeatureSnapshot.create(
        symbol=original.symbol,
        event_time=original.event_time,
        built_at=original.built_at,
        bid=original.bid,
        ask=original.ask,
        features={**original.features, "xv_coverage_score": 0},
        freshness=original.freshness,
    )

    result = run_once(
        database=tmp_path / "intraday.sqlite",
        snapshot=enriched,
        direction=Direction.BUY,
        cross_venue_mode="shadow",
        now=NOW,
    )

    assert result["cross_venue_mode"] == "shadow"
    assert result["cross_venue_status"] == "unavailable"
    assert result["cross_venue_applied"] is False


def test_pending_pause_command_is_applied_before_the_next_tick(tmp_path):
    database = tmp_path / "intraday.sqlite"
    store = IntradayStore(database)
    store.enqueue_command("cmd-pause", "pause_entries", NOW, actor="dashboard")

    result = run_once(
        database=database,
        snapshot=snapshot(),
        direction=Direction.BUY,
        now=NOW,
    )

    assert result["gate"] == "hold"
    assert "operator_pause" in result["reasons"]
    assert store.list_commands()[0]["status"] == "applied"


def test_flatten_command_closes_paper_position_and_pauses_reentry(tmp_path):
    database = tmp_path / "intraday.sqlite"
    run_once(database=database, snapshot=snapshot(), direction=Direction.BUY, now=NOW)
    store = IntradayStore(database)
    later = NOW + timedelta(seconds=5)
    store.enqueue_command("cmd-flat", "flatten", later, actor="dashboard")

    result = run_once(
        database=database,
        snapshot=snapshot(later),
        direction=Direction.BUY,
        now=later,
    )

    assert result["tranches"] == 0
    assert result["gate"] == "hold"
    assert "operator_flatten" in result["reasons"]
    assert store.counts()["fills"] == 2
