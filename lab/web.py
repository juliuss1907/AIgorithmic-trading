"""Local Vietnamese web workbench for inspecting completed experiments."""

import json
import os
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request

from lab.contracts import DatasetRequest, ExperimentSpec
from lab.ai import build_evidence_packet
from lab.comparison import compare_runs, load_experiment, validate_clone_config
from lab.data import DATA
from lab.dataset_jobs import DatasetJobQueue
from lab.datasets import DatasetCatalog
from lab.experiment import read_config
from lab.evaluation import PromotionStore
from lab.jobs import IdempotencyConflict, JobQueue
from lab.paper import PaperTradingService
from lab.store import ArtifactChanged, RunStore


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


def create_app(database=None, runs_dir=None, data_dir=None, promotion_database=None):
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
    paper = PaperTradingService(store.database)
    promotion_path = (
        promotion_database
        or os.environ.get("LAB_PROMOTION_STATE")
        or store.database.parent / "btc-promotion.sqlite3"
    )
    promotion = PromotionStore(promotion_path)
    app.state.store = store
    app.state.queue = queue
    app.state.dataset_queue = dataset_queue
    app.state.paper = paper
    app.state.promotion = promotion
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
            {"datasets": datasets, "defaults": read_config().to_json_dict(), "is_clone": False},
        )

    @app.get("/datasets")
    def datasets_page(request: Request):
        snapshots = [item.model_dump(mode="json") for item in catalog.list_ready()]
        return templates.TemplateResponse(
            request, "datasets.html",
            {"datasets": snapshots, "ready_symbols": {item["symbol"] for item in snapshots}},
        )

    @app.get("/paper")
    def paper_accounts_page(request: Request):
        try:
            gate = promotion.get()
        except KeyError:
            gate = None
        return templates.TemplateResponse(
            request, "paper.html", {"accounts": paper.list_accounts(), "gate": gate}
        )

    @app.get("/paper/{account_id}")
    def paper_account_page(request: Request, account_id: str):
        try:
            account = paper.get_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            campaign = paper.campaign_status(account_id)
        except KeyError:
            campaign = None
        return templates.TemplateResponse(request, "paper-account.html", {
            "account": account, "cycles": paper.list_cycles(account_id),
            "fills": paper.list_fills(account_id), "ledger": paper.list_ledger(account_id),
            "halts": paper.list_halts(account_id), "campaign": campaign,
            "ai_status": "disabled",
        })

    @app.get("/compare")
    def compare_page(request: Request, left_id: str | None = None, right_id: str | None = None):
        comparison = None
        error = None
        if left_id and right_id:
            try:
                comparison = compare_runs(store, left_id, right_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ArtifactChanged as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                error = str(exc)
        return templates.TemplateResponse(
            request, "compare.html",
            {"runs": store.list_runs(), "comparison": comparison, "error": error,
             "left_id": left_id, "right_id": right_id},
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

    @app.get("/runs/{run_id}/clone")
    def clone_experiment(request: Request, run_id: str):
        try:
            detail, config = load_experiment(store, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArtifactChanged as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        defaults = config.to_json_dict()
        defaults.update({
            "title": f"Bản sao · {config.title}"[:120],
            "hypothesis": "",
            "parent_run_id": run_id,
            "prior_observed_periods": list(config.periods),
        })
        dataset = {
            "id": config.dataset_id,
            "symbol": config.symbol,
            "start": str(config.data.start),
            "end": str(config.data.end_exclusive - timedelta(days=1)),
            "end_exclusive": str(config.data.end_exclusive),
        }
        return templates.TemplateResponse(
            request, "experiment-form.html",
            {"datasets": [dataset], "defaults": defaults, "is_clone": True,
             "parent": detail["run"]},
        )

    @app.get("/runs/{run_id}")
    def run_page(request: Request, run_id: str):
        detail = detail_or_404(run_id)
        detail["notes"] = store.list_notes(run_id)
        return templates.TemplateResponse(request, "run.html", detail)

    @app.get("/api/runs")
    def runs_api():
        return store.list_runs()

    @app.get("/api/compare")
    def compare_api(left_id: str, right_id: str):
        try:
            return compare_runs(store, left_id, right_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArtifactChanged as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/datasets")
    def datasets_api():
        return [item.model_dump(mode="json") for item in catalog.list_ready()]

    @app.get("/api/paper/accounts")
    def paper_accounts_api():
        return paper.list_accounts()

    @app.get("/api/paper/accounts/{account_id}")
    def paper_account_api(account_id: str):
        try:
            return paper.get_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/paper/accounts/{account_id}/campaign")
    def paper_campaign_api(account_id: str):
        try:
            return paper.campaign_status(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/paper/accounts/{account_id}/incidents")
    def paper_incidents_api(account_id: str):
        try:
            return paper.list_incidents(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/paper/accounts/{account_id}/cycles")
    def paper_cycles_api(account_id: str):
        try:
            return paper.list_cycles(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/paper/accounts/{account_id}/signals")
    def paper_signals_api(account_id: str):
        try:
            return paper.list_signals(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/paper/accounts/{account_id}/intents")
    def paper_intents_api(account_id: str):
        try:
            return paper.list_intents(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/paper/accounts/{account_id}/fills")
    def paper_fills_api(account_id: str):
        try:
            return paper.list_fills(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/paper/accounts/{account_id}/ledger")
    def paper_ledger_api(account_id: str):
        try:
            return paper.list_ledger(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/paper/accounts/{account_id}/evidence")
    def paper_evidence_api(account_id: str):
        try:
            return build_evidence_packet(paper, account_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/paper/accounts/{account_id}/halt")
    def paper_halt_api(account_id: str):
        try:
            return paper.halt_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/ai/status")
    def ai_status_api():
        return {"status": "disabled", "provider": "not-configured", "advisory_only": True}

    @app.get("/api/promotion")
    def promotion_api():
        try:
            return promotion.get()
        except KeyError:
            return {"status": "not_frozen", "selected": None, "scores": []}

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
            validate_clone_config(store, config)
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
