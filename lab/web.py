"""Local Vietnamese web workbench for inspecting completed experiments."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from lab.store import ArtifactChanged, RunStore


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


def create_app(database=None, runs_dir=None):
    app = FastAPI(title="Phòng thử nghiệm trading", version="0.1.0")
    store = RunStore(database=database, runs_dir=runs_dir)
    app.state.store = store
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

    @app.get("/runs/{run_id}")
    def run_page(request: Request, run_id: str):
        detail = detail_or_404(run_id)
        detail["notes"] = store.list_notes(run_id)
        return templates.TemplateResponse(request, "run.html", detail)

    @app.get("/api/runs")
    def runs_api():
        return store.list_runs()

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
