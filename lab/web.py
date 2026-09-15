"""Local Vietnamese web workbench for inspecting completed experiments."""

from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from lab.contracts import DatasetRequest, ExperimentSpec
from lab.data import DATA
from lab.dataset_jobs import DatasetJobQueue
from lab.datasets import DatasetCatalog
from lab.experiment import read_config
from lab.jobs import IdempotencyConflict, JobQueue
from lab.store import ArtifactChanged, RunStore


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


def create_app(database=None, runs_dir=None, data_dir=None):
    app = FastAPI(title="Phòng thử nghiệm trading", version="0.1.0")
    store = RunStore(database=database, runs_dir=runs_dir)
    catalog = DatasetCatalog(data_dir or DATA)
    if (catalog.base_dir / "manifest.json").exists():
        from lab.datasets import register_legacy_pilot
        register_legacy_pilot(catalog)
    queue = JobQueue(
        database=store.database,
        runs_dir=store.runs_dir,
        catalog=catalog,
    )
    dataset_queue = DatasetJobQueue(store.database, catalog)
    app.state.store = store
    app.state.queue = queue
    app.state.dataset_queue = dataset_queue
    app.state.sync_result = store.sync_runs()
    web_root = Path(__file__).with_name("web_assets")
    templates = Jinja2Templates(directory=web_root / "templates")
    app.mount("/static", StaticFiles(directory=web_root / "static"), name="static")

    def detail_or_404(run_id):
        try:
            return store.detail(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArtifactChanged as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/")
    def library(request: Request):
        return templates.TemplateResponse(
            request, "library.html", {"runs": store.list_runs(), "sync": app.state.sync_result}
        )

    @app.get("/experiments/new")
    def new_experiment(request: Request):
        datasets = []
        for snapshot in catalog.list_ready():
            item = snapshot.model_dump(mode="json")
            item["end_exclusive"] = str(snapshot.end + timedelta(days=1))
            datasets.append(item)
        return templates.TemplateResponse(
            request, "experiment-form.html",
            {"datasets": datasets, "defaults": read_config().to_json_dict()},
        )

    @app.get("/datasets")
    def datasets_page(request: Request):
        snapshots = [item.model_dump(mode="json") for item in catalog.list_ready()]
        return templates.TemplateResponse(
            request, "datasets.html",
            {"datasets": snapshots, "ready_symbols": {item["symbol"] for item in snapshots}},
        )

    @app.get("/dataset-jobs/{job_id}")
    def dataset_job_page(request: Request, job_id: str):
        try:
            job = dataset_queue.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(request, "dataset-job.html", {"job": job})

    @app.get("/jobs/{job_id}")
    def job_page(request: Request, job_id: str):
        try:
            job = queue.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(request, "job.html", {"job": job})

    @app.get("/runs/{run_id}")
    def run_page(request: Request, run_id: str):
        detail = detail_or_404(run_id)
        detail["notes"] = store.list_notes(run_id)
        return templates.TemplateResponse(request, "run.html", detail)

    @app.get("/api/runs")
    def runs_api():
        return store.list_runs()

    @app.get("/api/datasets")
    def datasets_api():
        return [item.model_dump(mode="json") for item in catalog.list_ready()]

    @app.post("/api/dataset-jobs", status_code=202)
    def create_dataset_job(
        request: DatasetRequest, idempotency_key: str | None = Header(default=None)
    ):
        try:
            return dataset_queue.enqueue(
                request, entry_point="web_api", idempotency_key=idempotency_key
            )
        except ValueError as exc:
            status = 409 if "different dataset request" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.get("/api/dataset-jobs/{job_id}")
    def dataset_job_api(job_id: str):
        try:
            return dataset_queue.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/dataset-jobs/{job_id}/retry", status_code=202)
    def retry_dataset_job(job_id: str):
        try:
            return dataset_queue.retry(job_id, entry_point="web_api_retry")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/jobs")
    def jobs_api():
        return queue.list()

    @app.post("/api/jobs", status_code=202)
    def create_job(config: ExperimentSpec, idempotency_key: str | None = Header(default=None)):
        try:
            return queue.enqueue(
                config, entry_point="web_api", idempotency_key=idempotency_key
            )
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def job_api(job_id: str):
        try:
            return queue.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str):
        try:
            return queue.retry(job_id, entry_point="web_api_retry")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def run_api(run_id: str):
        detail = detail_or_404(run_id)
        detail["notes"] = store.list_notes(run_id)
        return detail

    @app.post("/api/runs/{run_id}/notes", status_code=201)
    def add_note(run_id: str, request: NoteRequest):
        try:
            return store.add_note(run_id, request.body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/artifacts/{artifact_id}", name="artifact")
    def artifact(run_id: str, artifact_id: str):
        try:
            path, metadata = store.artifact(run_id, artifact_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ArtifactChanged, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(path, media_type=metadata["media_type"], filename=path.name)

    return app


app = create_app()
