"""Local-first monitoring dashboard and authenticated command inbox."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from intraday.contracts import DecisionScope, ProviderRole
from intraday.store import IntradayStore


ASSETS = Path(__file__).with_name("web_assets")


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=4, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    kind: str


class ProviderAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(
        min_length=3, max_length=80, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )


class PortfolioCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["pause", "resume", "flatten"]


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

    def queue_provider_command(
        *, idempotency_key: str, kind: str, payload: dict
    ) -> dict:
        if not 4 <= len(idempotency_key) <= 128 or not all(
            character.isalnum() or character in "._:-" for character in idempotency_key
        ):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid idempotency key")
        try:
            return store.enqueue_command(
                f"web:{idempotency_key}",
                kind,
                datetime.now(timezone.utc),
                actor="dashboard",
                payload=payload,
            )
        except ValueError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        runtime = store.load_runtime_state() or {}
        portfolio = store.load_portfolio_state() or {}
        cross_venue = store.venue_health("hyperliquid")
        cross_evaluation = store.latest_cross_venue_evaluation()
        provider_profiles = store.list_provider_profiles()
        assignments = {
            role.value: store.provider_assignment(role) for role in ProviderRole
        }
        analyst_reports = store.latest_analyst_reports()
        thesis = store.latest_market_thesis()
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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
                "provider_profiles": provider_profiles,
                "provider_assignments": assignments,
                "analyst_reports": analyst_reports,
                "thesis": thesis,
                "daily_model_cost": store.model_cost_since(day_start),
            },
        )

    @app.get("/portfolio", response_class=HTMLResponse)
    def portfolio_page(request: Request):
        parent = store.load_parent_portfolio_state()
        bundle = store.latest_market_thesis_bundle()
        return templates.TemplateResponse(
            request=request,
            name="portfolio.html",
            context={
                "portfolio": parent,
                "bundle": bundle,
                "spot_registry": store.scoped_rule_registry(DecisionScope.SPOT_DAILY),
                "perp_registry": store.scoped_rule_registry(
                    DecisionScope.PERP_INTRADAY
                ),
                "soak": store.latest_portfolio_soak_evaluation(),
                "events": store.list_parent_portfolio_events(limit=20),
            },
        )

    @app.get("/system-plan", response_class=HTMLResponse)
    def system_plan(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="system_plan.html",
            context={},
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

    @app.get("/api/portfolio")
    def parent_portfolio():
        parent = store.load_parent_portfolio_state()
        return {
            "portfolio": parent.model_dump(mode="json") if parent else None,
            "limits": {
                "spot_budget_pct": 0.60,
                "perp_budget_pct": 0.40,
                "spot_max_parent_equity_pct": 0.30,
                "perp_max_parent_equity_pct": 0.20,
                "gross_exposure_pct": 0.50,
                "abs_net_delta_pct": 0.50,
                "isolated_margin_pct": 0.10,
                "leverage": 3,
            },
            "soak": (
                store.latest_portfolio_soak_evaluation().model_dump(mode="json")
                if store.latest_portfolio_soak_evaluation()
                else None
            ),
        }

    @app.get("/api/decisions")
    def decisions(limit: int = Query(default=100, ge=1, le=1000)):
        return store.list_decisions(limit=limit)

    @app.get("/api/providers")
    def providers():
        return {
            "profiles": store.list_provider_profiles(),
            "assignments": {
                role.value: store.provider_assignment(role) for role in ProviderRole
            },
        }

    @app.get("/api/analysts")
    def analysts():
        reports = store.latest_analyst_reports()
        thesis = store.latest_market_thesis()
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {
            "reports": {
                name: report.model_dump(mode="json") for name, report in reports.items()
            },
            "thesis": thesis.model_dump(mode="json") if thesis else None,
            "daily_cost_usd": store.model_cost_since(day_start),
        }

    @app.post("/api/providers/{profile_id}/tests", status_code=status.HTTP_202_ACCEPTED)
    def test_provider(
        profile_id: str,
        _: None = Depends(require_control),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        if store.provider_profile(profile_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider profile")
        return queue_provider_command(
            idempotency_key=idempotency_key,
            kind="provider_test",
            payload={"profile_id": profile_id},
        )

    @app.put("/api/provider-assignments/{role}", status_code=status.HTTP_202_ACCEPTED)
    def activate_provider(
        role: ProviderRole,
        request: ProviderAssignmentRequest,
        _: None = Depends(require_control),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        profile = store.provider_profile(request.profile_id)
        if profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider profile")
        if profile.role != role:
            raise HTTPException(status.HTTP_409_CONFLICT, "provider role mismatch")
        return queue_provider_command(
            idempotency_key=idempotency_key,
            kind="provider_activate",
            payload={"role": role.value, "profile_id": request.profile_id},
        )

    @app.delete("/api/provider-assignments/{role}", status_code=status.HTTP_202_ACCEPTED)
    def deactivate_provider(
        role: ProviderRole,
        _: None = Depends(require_control),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        return queue_provider_command(
            idempotency_key=idempotency_key,
            kind="provider_deactivate",
            payload={"role": role.value},
        )

    @app.post(
        "/api/portfolio/commands", status_code=status.HTTP_202_ACCEPTED
    )
    def parent_portfolio_command(
        request: PortfolioCommandRequest,
        _: None = Depends(require_control),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        return queue_provider_command(
            idempotency_key=idempotency_key,
            kind=f"portfolio_{request.kind}",
            payload={},
        )

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
