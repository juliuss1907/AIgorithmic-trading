"""Local-first monitoring dashboard and authenticated command inbox."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from intraday.store import IntradayStore


ASSETS = Path(__file__).with_name("web_assets")


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=4, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    kind: str


def create_app(
    *,
    database: str | Path = "state/intraday/intraday.sqlite3",
    control_token: str | None = None,
    cross_venue_mode: str = "shadow",
) -> FastAPI:
    app = FastAPI(title="Crypto Intraday Control Room", version="0.1.0")
    store = IntradayStore(database)
    templates = Jinja2Templates(directory=ASSETS / "templates")
    app.mount("/static", StaticFiles(directory=ASSETS / "static"), name="static")
    app.state.store = store
    app.state.cross_venue_mode = cross_venue_mode

    def require_control(authorization: str | None = Header(default=None)) -> None:
        if control_token is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "controls are disabled")
        expected = f"Bearer {control_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "invalid control credential",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        runtime = store.load_runtime_state() or {}
        portfolio = store.load_portfolio_state() or {}
        cross_venue = store.venue_health("hyperliquid")
        cross_evaluation = store.latest_cross_venue_evaluation()
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "counts": store.counts(),
                "runtime": runtime,
                "portfolio": portfolio,
                "decisions": store.list_decisions(limit=12),
                "cross_venue": cross_venue,
                "cross_venue_mode": cross_venue_mode,
                "cross_evaluation": cross_evaluation,
            },
        )

    @app.get("/healthz")
    def health():
        return {"status": "ok", "mode": "paper"}

    @app.get("/api/status")
    def system_status():
        return {
            "mode": "paper",
            "counts": store.counts(),
            "runtime": store.load_runtime_state(),
            "portfolio": store.load_portfolio_state(),
            "cross_venue": store.venue_health("hyperliquid"),
            "cross_venue_mode": cross_venue_mode,
            "cross_venue_evaluation": (
                store.latest_cross_venue_evaluation().model_dump(mode="json")
                if store.latest_cross_venue_evaluation() else None
            ),
        }

    @app.get("/api/decisions")
    def decisions(limit: int = Query(default=100, ge=1, le=1000)):
        return store.list_decisions(limit=limit)

    @app.post("/api/commands", status_code=status.HTTP_202_ACCEPTED)
    def commands(command: CommandRequest, _: None = Depends(require_control)):
        return store.enqueue_command(
            command.command_id,
            command.kind,
            datetime.now(timezone.utc),
            actor="dashboard",
        )

    return app


app = create_app()
