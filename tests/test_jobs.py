from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from lab.contracts import ExperimentSpec
from lab.data import save_snapshot, sessions
from lab.datasets import DatasetCatalog
from lab.jobs import JobQueue, JobWorker
from lab.web import create_app


def configured_experiment(dataset_id):
    return ExperimentSpec.model_validate({
        "title": "Queued SPY check",
        "hypothesis": "The worker should preserve this exact request.",
        "symbol": "SPY",
        "dataset_id": dataset_id,
        "data": {"start": "2025-01-02", "end_exclusive": "2025-01-11"},
        "strategy": {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        "initial_cash": 1000,
        "slippage_bps": [0],
        "commission": 0,
        "periods": {"evaluation": ["2025-01-06", "2025-01-10"]},
    })


@pytest.fixture
def job_lab(tmp_path):
    data_dir = tmp_path / "data"
    catalog = DatasetCatalog(data_dir)
    index = sessions("2025-01-02", "2025-01-10")
    close = pd.Series(range(100, 100 + len(index)), index=index, dtype=float)
    raw = pd.DataFrame({
        "Open": close - 0.5, "High": close + 1, "Low": close - 1,
        "Close": close, "Adj Close": close * 0.9, "Volume": 1000,
    }, index=index)
    snapshot = save_snapshot(
        raw, configured_experiment(None), catalog,
        retrieved_at_utc=datetime(2025, 1, 11, tzinfo=timezone.utc).isoformat(),
    )
    queue = JobQueue(
        database=tmp_path / "state/lab.sqlite3",
        runs_dir=tmp_path / "runs",
        catalog=catalog,
    )
    return queue, configured_experiment(snapshot.id)


def test_worker_processes_jobs_one_at_a_time_with_frozen_inputs(job_lab):
    queue, config = job_lab
    first = queue.enqueue(config, entry_point="api")
    second = queue.enqueue(config.model_copy(update={"title": "Second run"}), entry_point="api")
    executed = []

    def execute(frozen, output):
        executed.append((frozen.title, output.name))
        return f"run-{output.name}"

    worker = JobWorker(queue, execute=execute)
    assert worker.run_once()["id"] == first["id"]
    assert queue.get(first["id"])["status"] == "completed"
    assert queue.get(second["id"])["status"] == "queued"
    assert worker.run_once()["id"] == second["id"]
    assert executed == [("Queued SPY check", first["id"]), ("Second run", second["id"])]
    assert queue.get(first["id"])["output_relative"] != queue.get(second["id"])["output_relative"]


def test_failure_never_creates_a_completed_result(job_lab):
    queue, config = job_lab
    job = queue.enqueue(config, entry_point="api")

    worker = JobWorker(queue, execute=lambda *_: (_ for _ in ()).throw(RuntimeError("audit failed")))
    worker.run_once()
    failed = queue.get(job["id"])

    assert failed["status"] == "failed"
    assert failed["result_run_id"] is None
    assert failed["error_type"] == "RuntimeError"
    assert "audit failed" in failed["error_message"]


def test_restart_marks_running_job_interrupted_and_retry_uses_new_output(job_lab):
    queue, config = job_lab
    original = queue.enqueue(config, entry_point="api")
    assert queue.claim_next()["status"] == "running"

    JobWorker(queue, execute=lambda *_: "unused")
    interrupted = queue.get(original["id"])
    retry = queue.retry(original["id"], entry_point="api_retry")

    assert interrupted["status"] == "interrupted"
    assert retry["status"] == "queued"
    assert retry["id"] != original["id"]
    assert retry["output_relative"] != original["output_relative"]
    assert retry["config"] == interrupted["config"]


def test_job_log_is_structured_and_correlated(job_lab):
    queue, config = job_lab
    job = queue.enqueue(config, entry_point="api")
    JobWorker(queue, execute=lambda *_: "run-id").run_once()

    events = queue.read_log(job["id"])
    assert [event["event"] for event in events] == ["job_queued", "job_started", "job_completed"]
    assert all(event["request_id"] == job["id"] for event in events)
    assert all(event["entry_point"] == "api" for event in events)


def test_api_returns_job_id_before_worker_runs(job_lab):
    queue, config = job_lab
    client = TestClient(create_app(
        database=queue.database, runs_dir=queue.runs_dir, data_dir=queue.catalog.base_dir
    ))

    response = client.post("/api/jobs", json=config.to_json_dict())
    assert response.status_code == 202
    job_id = response.json()["id"]
    assert response.json()["status"] == "queued"
    assert client.get(f"/api/jobs/{job_id}").json()["config"]["dataset_id"] == config.dataset_id


def test_default_worker_completes_only_after_report_and_audit(job_lab):
    queue, config = job_lab
    job = queue.enqueue(config, entry_point="integration_test")

    completed = JobWorker(queue).run_once()
    output = queue.output_path(completed)

    assert completed["status"] == "completed"
    assert completed["result_run_id"]
    assert (output / "report.md").is_file()
    assert (output / "evaluation/0bps/sma/audit.json").is_file()
    assert (output / "evaluation/0bps/buy-hold/audit.json").is_file()
