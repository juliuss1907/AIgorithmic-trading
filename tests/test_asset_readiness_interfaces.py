from datetime import datetime, timezone
import json
import sqlite3
import sys

from fastapi.testclient import TestClient
import pytest

from intraday.__main__ import main
from intraday.store import IntradayStore
from intraday.web import create_app


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def content(path):
    with sqlite3.connect(path) as connection:
        return list(connection.iterdump())


def test_readiness_api_filters_public_read_only_and_no_network(tmp_path, monkeypatch):
    path = tmp_path / "source.sqlite"
    client = TestClient(create_app(database=path))
    store = IntradayStore(path)
    store.register_asset("DOGE", market="spot", now=NOW)
    before = content(path)
    import intraday.asset_readiness as readiness
    original = readiness.build_asset_readiness
    monkeypatch.setattr("intraday.web.build_asset_readiness", lambda database, **kw: original(database, now=NOW, **kw))
    report = client.get("/api/assets/readiness?symbol=doge&market=spot")
    assert report.status_code == 200
    assert report.json() == original(path, symbol="DOGE", market="spot", now=NOW)
    assert client.get("/api/assets/readiness?market=perp").status_code == 200
    assert client.get("/api/assets/readiness?market=bad").status_code == 422
    assert client.get("/api/assets/readiness?symbol=UNKNOWN").status_code == 404
    assert client.get("/api/assets/readiness?symbol=ETH%3B").status_code == 422
    assert content(path) == before


def test_readiness_cli_json_and_table_preserve_source(tmp_path, monkeypatch, capsys):
    path = tmp_path / "source.sqlite"
    store = IntradayStore(path)
    store.register_asset("DOGE", market="spot", now=NOW)
    before = content(path)
    for extra in (["--json"], []):
        monkeypatch.setattr(sys, "argv", ["aigt", "assets", "readiness", "doge", "--market", "spot", "--database", str(path), *extra])
        main()
        output = capsys.readouterr().out
        if extra:
            assert json.loads(output)["rows"][0]["symbol"] == "DOGEUSDT"
        else:
            assert "DOGEUSDT" in output and "Spot" in output
            assert "không phải quyền trading" in output
    assert content(path) == before


def test_readiness_cli_missing_database_does_not_initialize(tmp_path, monkeypatch):
    path = tmp_path / "missing" / "source.sqlite"
    monkeypatch.setattr(sys, "argv", ["aigt", "assets", "readiness", "--database", str(path)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code != 0
    assert not path.exists()


def test_readiness_cli_routes_to_registered_admin(tmp_path, monkeypatch):
    from intraday.deployment import Deployment
    import intraday.deployment as deployment
    registered = Deployment(tmp_path, tmp_path / "compose.yaml", tmp_path / "config.intraday")
    calls = []
    monkeypatch.setattr(deployment, "load_deployment", lambda: registered)
    monkeypatch.setattr(deployment, "execute", lambda command, **kw: calls.append((command, kw)) or 0)
    monkeypatch.setattr(sys, "argv", ["aigt", "assets", "readiness", "ETH", "--market", "perp", "--json"])
    main()
    assert calls == [(deployment.admin_command(registered, sys.argv[1:]), {"cwd": registered.project_root})]
