import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lab.paper import PaperTradingService
from lab.store import RunStore
from lab.web import create_app


def make_run(runs_dir, name="pilot"):
    root = runs_dir / name
    case = root / "evaluation/5bps/sma"
    case.mkdir(parents=True)
    summary = {
        "evaluation/5bps/sma": {
            "final_equity": 1082.6,
            "total_return": 0.0826,
            "cagr_252": 0.02,
            "max_drawdown": -0.2816,
            "round_trips": 2,
            "fills": 4,
            "time_in_market": 0.5,
            "mean_holding_sessions": 20.0,
            "sessions": 100,
            "start": "2022-01-03",
            "end": "2022-05-25",
        }
    }
    provenance = {
        "experiment": {
            "title": "SPY SMA test",
            "hypothesis": "Trend may reduce drawdown.",
            "symbol": "SPY",
            "strategy": {"fast_window": 20, "slow_window": 50},
        },
        "data": {
            "symbol": "SPY",
            "source": "fixture",
            "start": "2021-01-01",
            "end": "2022-05-25",
            "rows": 350,
            "adjustment": "fixture",
            "files": {"raw.csv": "a" * 64, "adjusted.csv": "b" * 64},
        },
    }
    (root / "summary.json").write_text(json.dumps(summary))
    (root / "provenance.json").write_text(json.dumps(provenance))
    (root / "report.md").write_text("# Report\n")
    (root / "equity.png").write_bytes(b"fake png")
    (case / "summary.json").write_text(json.dumps(next(iter(summary.values()))))
    (case / "audit.json").write_text(json.dumps({"fills_checked": 2}))
    (case / "equity.csv").write_text("date,equity\n2022-01-03,1000\n")
    (case / "fills-exact.csv").write_text(
        "symbol,timestamp,bar_idx,action,signed_quantity,notional,execution_price,fee,margin,reason,holding_bars\n"
        "SPY.US,2022-01-03,0,BUY,10,1000,100,0,0,signal,0\n"
    )
    return root


@pytest.fixture
def web_lab(tmp_path):
    runs_dir = tmp_path / "runs"
    make_run(runs_dir)
    database = tmp_path / "state/lab.sqlite3"
    return TestClient(create_app(database=database, runs_dir=runs_dir)), database, runs_dir


def test_library_and_detail_show_registered_evidence(web_lab):
    client, _, _ = web_lab

    library = client.get("/")
    assert library.status_code == 200
    assert "SPY SMA test" in library.text
    run = client.get("/api/runs").json()[0]
    detail = client.get(f"/api/runs/{run['id']}")

    assert detail.status_code == 200
    assert detail.json()["summary"]["evaluation/5bps/sma"]["final_equity"] == 1082.6
    assert detail.json()["fills"][0]["action"] == "BUY"
    chart = next(item for item in detail.json()["artifacts"] if item["label"] == "equity.png")
    assert client.get(f"/api/runs/{run['id']}/artifacts/{chart['id']}").content == b"fake png"


def test_notes_persist_without_changing_results(web_lab):
    client, database, runs_dir = web_lab
    run = client.get("/api/runs").json()[0]
    summary_before = (runs_dir / "pilot/summary.json").read_bytes()

    response = client.post(f"/api/runs/{run['id']}/notes", json={"body": "  Kiểm tra regime khác.  "})
    assert response.status_code == 201
    assert response.json()["body"] == "Kiểm tra regime khác."

    restarted = TestClient(create_app(database=database, runs_dir=runs_dir))
    detail = restarted.get(f"/api/runs/{run['id']}").json()
    assert detail["notes"][0]["body"] == "Kiểm tra regime khác."
    assert (runs_dir / "pilot/summary.json").read_bytes() == summary_before
    assert restarted.post(f"/api/runs/{run['id']}", json={}).status_code == 405


def test_only_registered_unchanged_artifacts_are_served(web_lab):
    client, _, runs_dir = web_lab
    run = client.get("/api/runs").json()[0]
    detail = client.get(f"/api/runs/{run['id']}").json()
    report = next(item for item in detail["artifacts"] if item["label"] == "report.md")

    assert client.get(f"/api/runs/{run['id']}/artifacts/not-registered").status_code == 404
    (runs_dir / "pilot/report.md").write_text("changed")
    response = client.get(f"/api/runs/{run['id']}/artifacts/{report['id']}")
    assert response.status_code == 409


def test_incomplete_run_is_not_imported(tmp_path):
    runs_dir = tmp_path / "runs"
    incomplete = runs_dir / "partial"
    incomplete.mkdir(parents=True)
    (incomplete / "summary.json").write_text("{}")
    store = RunStore(tmp_path / "state/lab.sqlite3", runs_dir)

    with pytest.raises(ValueError, match="missing artifacts"):
        store.import_run(incomplete)
    assert store.list_runs() == []


def test_store_expands_legacy_runs_table_without_losing_rows(tmp_path):
    database = tmp_path / "state/lab.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, run_name TEXT NOT NULL, title TEXT NOT NULL,
                hypothesis TEXT NOT NULL, symbol TEXT NOT NULL, strategy_label TEXT NOT NULL,
                dataset_id TEXT, relative_path TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK (status = 'completed'), created_at TEXT NOT NULL
            )
        """)
        connection.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("a" * 20, "legacy", "Legacy run", "Keep me", "SPY", "SMA 20/50", None,
             "legacy", "completed", "2025-01-01T00:00:00+00:00"),
        )

    store = RunStore(database, tmp_path / "runs")

    assert store.get_run("a" * 20)["parent_run_id"] is None


def test_imported_clone_preserves_parent_lineage(tmp_path):
    runs_dir = tmp_path / "runs"
    store = RunStore(tmp_path / "state/lab.sqlite3", runs_dir)
    parent = store.import_run(make_run(runs_dir, "parent"))
    child_path = make_run(runs_dir, "child")
    provenance_path = child_path / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["experiment"]["parent_run_id"] = parent["id"]
    provenance_path.write_text(json.dumps(provenance))

    child = store.import_run(child_path)

    assert child["parent_run_id"] == parent["id"]
    assert store.get_run(parent["id"])["parent_run_id"] is None


def test_paper_dashboard_and_audit_apis(tmp_path):
    database = tmp_path / "state/lab.sqlite3"
    runs_dir = tmp_path / "runs"
    data_dir = tmp_path / "data"
    paper = PaperTradingService(database)
    account = paper.create_account(
        {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        dataset_snapshot_id="a" * 64,
    )
    client = TestClient(create_app(database=database, runs_dir=runs_dir, data_dir=data_dir))

    listing = client.get("/paper")
    detail = client.get(f"/paper/{account['id']}")

    assert listing.status_code == 200
    assert "Paper trading" in listing.text
    assert detail.status_code == 200
    assert "10,000.00 USDT" in detail.text
    assert client.get("/api/paper/accounts").json()[0]["id"] == account["id"]
    assert client.get(f"/api/paper/accounts/{account['id']}/cycles").json() == []
    assert client.get("/api/ai/status").json()["status"] == "disabled"

    stopped = client.post(f"/api/paper/accounts/{account['id']}/halt")
    assert stopped.status_code == 200
    assert stopped.json()["halt_reason"] == "manual_kill_switch"
