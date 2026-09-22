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


def test_clone_form_locks_shared_conditions_and_marks_prior_periods_observed(tmp_path):
    client, runs, _, _ = comparison_lab(tmp_path)
    parent = runs["left"]

    response = client.get(f"/runs/{parent['id']}/clone")

    assert response.status_code == 200
    assert "Nhân bản thí nghiệm" in response.text
    assert "Bản sao · SMA 20/50" in response.text
    assert 'id="dataset"' in response.text and "disabled" in response.text
    assert 'id="initial_cash"' in response.text and "readonly" in response.text
    assert f"const parentRunId = '{parent['id']}'" in response.text
    assert 'const priorObservedPeriods = ["evaluation"]' in response.text


def test_clone_api_rejects_changes_to_shared_conditions(tmp_path):
    client, runs, left_path, _ = comparison_lab(tmp_path)
    parent = runs["left"]
    payload = json.loads((left_path / "provenance.json").read_text())["experiment"]
    payload.update({
        "title": "Changed capital", "hypothesis": "Try a different rule.",
        "parent_run_id": parent["id"], "prior_observed_periods": ["evaluation"],
        "initial_cash": 2000,
    })

    response = client.post(
        "/api/jobs", json=payload, headers={"Idempotency-Key": "clone-changed-capital"}
    )

    assert response.status_code == 422
    assert "initial_cash" in response.json()["detail"]


def test_compare_page_supports_free_selection_and_compatible_deltas(tmp_path):
    client, runs, _, _ = comparison_lab(tmp_path)

    picker = client.get("/compare")
    result = client.get(
        "/compare", params={"left_id": runs["left"]["id"], "right_id": runs["right"]["id"]}
    )

    assert picker.status_code == 200
    assert "Chọn hai run" in picker.text
    assert "SMA 20/50" in picker.text and "SMA 10/50" in picker.text
    assert result.status_code == 200
    assert "Cùng điều kiện" in result.text
    assert "+5.00%" in result.text
    assert f'/runs/{runs["left"]["id"]}/clone' in result.text


def test_compare_page_explains_incompatibility_without_a_ranking(tmp_path):
    client, runs, _, _ = comparison_lab(tmp_path, different_conditions=True)

    result = client.get(
        "/compare", params={"left_id": runs["left"]["id"], "right_id": runs["right"]["id"]}
    )

    assert result.status_code == 200
    assert "Không xếp hạng" in result.text
    assert "Dataset" in result.text
    assert "Vốn giả lập" in result.text
    assert "+5.00%" not in result.text


def test_run_detail_offers_clone_action(tmp_path):
    client, runs, _, _ = comparison_lab(tmp_path)
    run_id = runs["left"]["id"]

    page = client.get(f"/runs/{run_id}")

    assert page.status_code == 200
    assert f'href="/runs/{run_id}/clone"' in page.text
