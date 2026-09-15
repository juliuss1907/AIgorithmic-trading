from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from lab.contracts import DatasetRequest, ExperimentSpec
from lab.data import save_snapshot, sessions
from lab.dataset_jobs import DatasetJobQueue, DatasetWorker
from lab.datasets import DatasetCatalog
from lab.jobs import JobQueue, JobWorker
from lab.web import create_app


def bars(offset=0):
    index = sessions("2025-01-02", "2025-01-10")
    close = pd.Series(range(100 + offset, 100 + offset + len(index)), index=index, dtype=float)
    return pd.DataFrame({
        "Open": close - .5, "High": close + 1, "Low": close - 1,
        "Close": close, "Adj Close": close * .9, "Volume": 1000,
    }, index=index)


def request(symbol="QQQ"):
    return DatasetRequest.model_validate({
        "symbol": symbol, "start": "2025-01-02", "end_exclusive": "2025-01-11"
    })


def experiment(snapshot):
    return ExperimentSpec.model_validate({
        "title": "QQQ worker audit", "hypothesis": "QQQ trend may persist.",
        "symbol": "QQQ", "dataset_id": snapshot.id,
        "data": {"start": "2025-01-02", "end_exclusive": "2025-01-11"},
        "strategy": {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        "initial_cash": 1000, "slippage_bps": [0], "commission": 0,
        "periods": {"evaluation": ["2025-01-06", "2025-01-10"]},
    })


@pytest.fixture
def dataset_lab(tmp_path):
    catalog = DatasetCatalog(tmp_path / "data")
    spy_config = ExperimentSpec.model_validate({
        "title": "SPY fixture", "hypothesis": "Existing data must survive.", "symbol": "SPY",
        "data": {"start": "2025-01-02", "end_exclusive": "2025-01-11"},
        "strategy": {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        "initial_cash": 1000, "slippage_bps": [0], "commission": 0,
        "periods": {"evaluation": ["2025-01-06", "2025-01-10"]},
    })
    spy = save_snapshot(
        bars(), spy_config, catalog,
        retrieved_at_utc=datetime(2025, 1, 11, tzinfo=timezone.utc).isoformat(),
    )
    database = tmp_path / "state/lab.sqlite3"
    return catalog, database, tmp_path / "runs", spy


def test_dataset_worker_registers_the_requested_symbol(dataset_lab):
    catalog, database, _, spy = dataset_lab
    requested = []

    def downloader(spec):
        requested.append(spec.symbol)
        return bars(200)

    queue = DatasetJobQueue(database, catalog, downloader=downloader)
    job = queue.enqueue(request(), entry_point="api", idempotency_key="qqq-download-1")
    completed = DatasetWorker(queue).run_once()
    snapshot = catalog.get(completed["result_dataset_id"])

    assert job["status"] == "queued"
    assert completed["status"] == "completed"
    assert requested == ["QQQ"]
    assert snapshot.symbol == "QQQ"
    assert snapshot.id != spy.id


def test_failed_download_does_not_publish_or_damage_existing_snapshot(dataset_lab):
    catalog, database, _, spy = dataset_lab
    queue = DatasetJobQueue(
        database, catalog,
        downloader=lambda _: (_ for _ in ()).throw(ConnectionError("Yahoo unavailable")),
    )
    job = queue.enqueue(request(), entry_point="api")

    failed = DatasetWorker(queue).run_once()

    assert failed["id"] == job["id"]
    assert failed["status"] == "failed"
    assert catalog.list_ready("QQQ") == []
    assert catalog.load(spy.id)[0].iloc[0]["close"] == pytest.approx(90)


def test_incomplete_qqq_calendar_is_not_ready(dataset_lab):
    catalog, database, _, _ = dataset_lab
    queue = DatasetJobQueue(database, catalog, downloader=lambda _: bars(200).iloc[1:])
    queue.enqueue(request(), entry_point="api")

    failed = DatasetWorker(queue).run_once()

    assert failed["status"] == "failed"
    assert "Session mismatch" in failed["error_message"]
    assert catalog.list_ready("QQQ") == []


def test_dataset_job_restart_and_retry_gets_a_new_id(dataset_lab):
    catalog, database, _, _ = dataset_lab
    queue = DatasetJobQueue(database, catalog, downloader=lambda _: bars(200))
    original = queue.enqueue(request(), entry_point="api")
    queue.claim_next()

    DatasetWorker(queue)
    retry = queue.retry(original["id"], entry_point="api_retry")

    assert queue.get(original["id"])["status"] == "interrupted"
    assert retry["id"] != original["id"]


def test_dataset_api_is_idempotent_and_lists_ready_snapshots(dataset_lab):
    catalog, database, runs_dir, _ = dataset_lab
    client = TestClient(create_app(database, runs_dir, catalog.base_dir))
    headers = {"Idempotency-Key": "qqq-download-api"}

    first = client.post("/api/dataset-jobs", json=request().model_dump(mode="json"), headers=headers)
    second = client.post("/api/dataset-jobs", json=request().model_dump(mode="json"), headers=headers)

    assert first.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    snapshots = client.get("/api/datasets").json()
    assert [item["symbol"] for item in snapshots] == ["SPY"]


def test_qqq_snapshot_runs_with_qqq_engine_symbol_and_audit(dataset_lab):
    catalog, database, runs_dir, _ = dataset_lab
    datasets = DatasetJobQueue(database, catalog, downloader=lambda _: bars(200))
    datasets.enqueue(request(), entry_point="test")
    snapshot = catalog.get(DatasetWorker(datasets).run_once()["result_dataset_id"])
    runs = JobQueue(database, runs_dir, catalog)
    runs.enqueue(experiment(snapshot), entry_point="test")

    completed = JobWorker(runs).run_once()
    output = runs.output_path(completed)

    assert completed["status"] == "completed"
    assert '"codes": [\n    "QQQ.US"' in (output / "evaluation/0bps/sma/config.json").read_text()
    assert (output / "evaluation/0bps/sma/audit.json").is_file()
