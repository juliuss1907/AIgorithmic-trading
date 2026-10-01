"""Read-only report API and server-rendered research pages, independent of source DB."""

from datetime import datetime
import math

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from intraday.replay_v2.artifacts import list_reports, read_report, read_series


def equity_chart(points):
    if not points:
        return None
    values = [float(point["equity_known"]) for point in points]
    times = [datetime.fromisoformat(point["at"]).timestamp() for point in points]
    if not all(math.isfinite(value) for value in values+times):
        raise ValueError("invalid chart data")
    low, high = min(values), max(values)
    padding = max((high-low)*.05, abs(low)*.001, .01)
    low, high = low-padding, high+padding
    span = max(times[-1]-times[0], 1)
    plot = " ".join(f"{(at-times[0])/span*1000:.3f},{190-(value-low)/(high-low)*180:.3f}"
                    for at,value in zip(times, values))
    return {"plot":plot, "minimum":low, "maximum":high, "first_at":points[0]["at"], "last_at":points[-1]["at"]}


def replay_router(root, templates):
    router = APIRouter()

    def lookup(run_id):
        try:
            return read_report(root, run_id)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, "Replay report unavailable or checksum invalid") from None

    def series(run_id, name, offset, limit):
        try:
            return read_series(root, run_id, name, offset=offset, limit=limit)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, "Replay ledger unavailable or checksum invalid") from None

    def listing(offset, limit):
        try:
            return list_reports(root, offset=offset, limit=limit)
        except OSError:
            raise HTTPException(503, "Replay report directory unavailable") from None

    @router.get("/api/replay/runs")
    def api_list(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return listing(offset, limit)

    @router.get("/api/replay/runs/{run_id}")
    def api_detail(run_id: str):
        return lookup(run_id)

    @router.get("/api/replay/runs/{run_id}/{name}")
    def api_series(run_id: str, name: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        return series(run_id, name, offset, limit)

    @router.get("/replay", response_class=HTMLResponse)
    def reports_page(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return templates.TemplateResponse(request=request, name="replay.html", context={"listing":listing(offset, limit)})

    @router.get("/replay/{run_id}", response_class=HTMLResponse)
    def report_page(request: Request, run_id: str, trades_offset: int = Query(0, ge=0), events_offset: int = Query(0, ge=0)):
        report = lookup(run_id)
        try:
            chart = equity_chart(report["equity_preview"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(404, "Replay chart unavailable") from None
        return templates.TemplateResponse(request=request, name="replay_run.html", context={
            "report":report, "chart":chart,
            "trades":series(run_id, "trades", trades_offset, 20),
            "events":series(run_id, "events", events_offset, 30)})

    return router
