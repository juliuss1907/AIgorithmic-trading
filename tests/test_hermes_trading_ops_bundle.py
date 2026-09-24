import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "integrations" / "hermes" / "trading-ops"


def _load_module(name: str, path: Path, *, package=False):
    kwargs = {"submodule_search_locations": [str(path.parent)]} if package else {}
    spec = importlib.util.spec_from_file_location(name, path, **kwargs)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_distribution_is_installable_but_contains_no_runtime_secrets():
    manifest = (BUNDLE / "distribution.yaml").read_text()
    config = (BUNDLE / "config.yaml").read_text()
    env_template = (BUNDLE / ".env.template").read_text()

    assert "name: trading-ops" in manifest
    assert 'hermes_requires: ">=0.20.4"' in manifest
    assert "aigt_operator" in config
    assert "AIGT_OPERATOR_READ_TOKEN=" in env_template
    assert "AIGT_OPERATOR_ACTION_TOKEN=" in env_template
    assert "replace-with" not in env_template
    assert (BUNDLE / "SOUL.md").is_file()
    assert (BUNDLE / "skills" / "aigt-operator" / "SKILL.md").is_file()


def test_operator_client_is_loopback_only_and_uses_scoped_credentials():
    client_module = _load_module(
        "aigt_operator_client", BUNDLE / "plugins" / "aigt_operator" / "client.py"
    )
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body, timeout))
        return json.dumps({"schema_version": "1", "mode": "paper"}).encode()

    client = client_module.OperatorClient(
        base_url="http://127.0.0.1:8081",
        read_token="read-secret",
        action_token="action-secret",
        transport=transport,
    )
    assert client.snapshot()["mode"] == "paper"
    client.create_action("pause", idempotency_key="telegram-update-1")

    assert calls[0][2]["Authorization"] == "Bearer read-secret"
    assert calls[1][2]["Authorization"] == "Bearer action-secret"
    assert json.loads(calls[1][3]) == {"action": "pause"}

    with pytest.raises(ValueError, match="loopback"):
        client_module.OperatorClient(
            base_url="https://operator.example.com",
            read_token="read-secret",
            action_token="action-secret",
        )


def test_plugin_registers_one_read_only_tool_and_deterministic_commands(monkeypatch):
    package_name = "aigt_operator_plugin"
    client_module = _load_module(
        f"{package_name}.client",
        BUNDLE / "plugins" / "aigt_operator" / "client.py",
    )
    monkeypatch.setitem(__import__("sys").modules, f"{package_name}.client", client_module)
    plugin = _load_module(
        package_name,
        BUNDLE / "plugins" / "aigt_operator" / "__init__.py",
        package=True,
    )

    class FakeContext:
        def __init__(self):
            self.tools = []
            self.commands = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

        def register_command(self, name, handler, description="", args_hint=""):
            self.commands.append(name)

    context = FakeContext()
    plugin.register(context)

    assert [tool["name"] for tool in context.tools] == ["aigt_operator_read"]
    assert set(context.commands) == {
        "trade-status",
        "trade-risk",
        "trade-health",
        "trade-experiment",
        "trade-rules",
        "trade-cost",
        "trade-pause",
        "trade-resume",
        "trade-approve",
        "trade-cancel",
    }
    assert "action" not in json.dumps(context.tools[0]["schema"])


def test_alert_watcher_persists_cursor_and_is_silent_without_new_alerts(tmp_path, monkeypatch):
    scripts = BUNDLE / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    watcher = _load_module("aigt_alert_watch", scripts / "aigt_alert_watch.py")

    class FakeClient:
        def __init__(self):
            self.responses = [
                {
                    "alerts": [
                        {
                            "id": 7,
                            "severity": "critical",
                            "kind": "scheduler_error",
                            "message": "Scheduler failed.",
                            "created_at": "2026-09-24T02:00:00+00:00",
                        }
                    ],
                    "next_cursor": 7,
                },
                {"alerts": [], "next_cursor": 7},
            ]

        def alerts(self, after_id):
            assert after_id in {0, 7}
            return self.responses.pop(0)

    cursor = tmp_path / "cursor"
    client = FakeClient()

    first = watcher.collect_alerts(client, cursor)
    second = watcher.collect_alerts(client, cursor)

    assert "CRITICAL" in first
    assert "scheduler_error" in first
    assert second == ""
    assert cursor.read_text() == "7\n"


def test_alert_watcher_deduplicates_outage_and_reports_recovery(tmp_path, monkeypatch):
    scripts = BUNDLE / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    watcher = _load_module("aigt_alert_watch_health", scripts / "aigt_alert_watch.py")

    def failing_client():
        raise RuntimeError("operator_unavailable")

    class HealthyClient:
        def alerts(self, after_id):
            return {"alerts": [], "next_cursor": after_id}

    cursor = tmp_path / "cursor"
    health = tmp_path / "health"

    assert "CRITICAL" in watcher.watch_once(failing_client, cursor, health)
    assert watcher.watch_once(failing_client, cursor, health) == ""
    assert "recovered" in watcher.watch_once(HealthyClient, cursor, health)
