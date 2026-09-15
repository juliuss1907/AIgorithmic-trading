import json

from fastapi.testclient import TestClient

from lab.web import create_app


def write_run(runs_dir, name, *, title, dataset_id, fast, total_return, initial_cash=1000):
    root = runs_dir / name
    case = root / "evaluation/5bps/sma"
    case.mkdir(parents=True)
    metric = {
        "final_equity": initial_cash * (1 + total_return),
        "total_return": total_return,
        "cagr_252": total_return / 2,
        "max_drawdown": -0.2 + total_return / 10,
        "round_trips": fast,
        "fills": fast * 2,
        "time_in_market": 0.5,
        "mean_holding_sessions": 20.0,
        "sessions": 100,
        "start": "2024-03-01",
        "end": "2024-12-31",
    }
    summary = {"evaluation/5bps/sma": metric}
    provenance = {
        "experiment": {
            "title": title,
            "hypothesis": f"Test {title}",
            "symbol": "SPY",
            "dataset_id": dataset_id,
            "data": {"start": "2024-01-01", "end_exclusive": "2025-01-01"},
            "strategy": {"family": "sma_crossover", "fast_window": fast, "slow_window": 50},
            "initial_cash": initial_cash,
            "slippage_bps": [5],
            "commission": 0,
            "periods": {"evaluation": ["2024-03-01", "2024-12-31"]},
        },
        "data": {"symbol": "SPY"},
    }
    (root / "summary.json").write_text(json.dumps(summary))
    (root / "provenance.json").write_text(json.dumps(provenance))
    (root / "report.md").write_text("# Report\n")
    (root / "equity.png").write_bytes(b"fake png")
    (case / "summary.json").write_text(json.dumps(metric))
    (case / "audit.json").write_text(json.dumps({"fills_checked": fast * 2}))
    (case / "equity.csv").write_text("date,equity\n2024-03-01,1000\n")
    (case / "fills-exact.csv").write_text(
        "symbol,timestamp,action,signed_quantity,execution_price,reason\n"
    )
    return root


def comparison_lab(tmp_path, *, different_conditions=False):
    runs_dir = tmp_path / "runs"
    left_path = write_run(
        runs_dir, "left", title="SMA 20/50", dataset_id="a" * 64,
        fast=20, total_return=0.10,
    )
    right_path = write_run(
        runs_dir, "right", title="SMA 10/50", dataset_id="b" * 64 if different_conditions else "a" * 64,
        fast=10, total_return=0.15, initial_cash=2000 if different_conditions else 1000,
    )
    client = TestClient(create_app(
        database=tmp_path / "state/lab.sqlite3", runs_dir=runs_dir, data_dir=tmp_path / "data"
    ))
    runs = {item["run_name"]: item for item in client.get("/api/runs").json()}
    return client, runs, left_path, right_path


def test_compatible_runs_return_case_deltas_without_an_overall_winner(tmp_path):
    client, runs, _, _ = comparison_lab(tmp_path)

    response = client.get(
        "/api/compare", params={"left_id": runs["left"]["id"], "right_id": runs["right"]["id"]}
    )

    assert response.status_code == 200
    comparison = response.json()
    assert comparison["compatible"] is True
    assert comparison["differences"] == []
    assert comparison["cases"][0]["delta"]["total_return"] == 0.05
    assert "winner" not in comparison


def test_incompatible_runs_are_shown_without_ranking_or_deltas(tmp_path):
    client, runs, _, _ = comparison_lab(tmp_path, different_conditions=True)

    response = client.get(
        "/api/compare", params={"left_id": runs["left"]["id"], "right_id": runs["right"]["id"]}
    )

    assert response.status_code == 200
    comparison = response.json()
    assert comparison["compatible"] is False
    assert {item["field"] for item in comparison["differences"]} == {"dataset_id", "initial_cash"}
    assert comparison["cases"][0]["delta"] is None


def test_comparison_rejects_same_or_unknown_run(tmp_path):
    client, runs, _, _ = comparison_lab(tmp_path)
    run_id = runs["left"]["id"]

    assert client.get("/api/compare", params={"left_id": run_id, "right_id": run_id}).status_code == 422
    assert client.get("/api/compare", params={"left_id": run_id, "right_id": "f" * 20}).status_code == 404


def test_comparison_refuses_changed_provenance(tmp_path):
    client, runs, left_path, _ = comparison_lab(tmp_path)
    (left_path / "provenance.json").write_text("{}")

    response = client.get(
        "/api/compare", params={"left_id": runs["left"]["id"], "right_id": runs["right"]["id"]}
    )

    assert response.status_code == 409
