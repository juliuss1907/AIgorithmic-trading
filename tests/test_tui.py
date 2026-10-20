import asyncio
import io
import subprocess

import pytest
from rich.console import Console

pytest.importorskip("textual")

from intraday.__main__ import _parser
from intraday.tui import candles
from intraday.tui.app import AigtApp
from intraday.tui.remote import Remote, RemoteError

SIGNAL = dict(route="ZECUSDT:perp", bar_close="2026-10-18T20:00:00+00:00", side=-1, close="1055.48",
              jev_status="called", jev=dict(choice="Buy", confidence=0.23, stance="oppose"), hour_score_pct=-0.19,
              with_hours=False, simulated=dict(status="stop", exit_at=None, gross_return_pct=-5.0),
              limit=dict(status="filled", fill="1065.2", diff_pct=0.58))
EMPTY = dict(signals=0, wins=0, n=0, mean_return_pct=None, standard_error_pct=None, t_stat=None)
SETUP2 = dict(
    generated_at="2026-10-20T04:05:00+00:00", since="2026-10-14T04:09:00+00:00",
    health=dict(last_run="2026-10-20T04:04:58+00:00", age_seconds=2.0, stale=False,
                errors=[dict(job_name="asset_spot_4h_hypeusdt", error_code="StaleMarketData", n=6,
                             last="2026-10-20T04:00:00+00:00")]),
    campaigns=[dict(route=r, symbol=r.split(":")[0], market=r.split(":")[1], started_at="2026-10-14T04:09:00+00:00",
                    coverage_pct=c, healthy=c >= 95, bars=36, jev_errors=0, last_bar="2026-10-20T00:00:00+00:00",
                    review_at="2026-10-28T04:09:00+00:00", progress_pct=43.0)
               for r, c in (("ETHUSDT:perp", 97.2), ("HYPEUSDT:spot", 0.0), ("ZECUSDT:perp", 100.0))],
    latest_bars=[dict(route="ZECUSDT:perp", bar_close="2026-10-20T04:00:00+00:00", close="1148.35000000",
                      entry=False, breakout=True, exit=False, failed_filters=["adx"], blockers=[],
                      jev_status="skipped_no_setup")],
    signals=[SIGNAL],
    hour_score=dict(with_hours=EMPTY, against_hours=dict(EMPTY, signals=4, n=4, mean_return_pct=-4.94,
                                                         standard_error_pct=0.05), unscored=EMPTY),
    limit_entry=dict(signals=4, filled=4, unfilled=0, pending=0, no_data=0, better=2, n=4, mean_return_pct=0.949,
                     standard_error_pct=0.664, t_stat=1.4293),
    adx_rise=dict(summary=dict(added=dict(EMPTY, signals=1, n=1, mean_return_pct=2.5, standard_error_pct=None),
                               dropped=EMPTY, no_data=0),
                  items=[dict(id="adxrise:SOLUSDT:perp:x", symbol="SOLUSDT", market="perp",
                              bar_close="2026-10-19T08:00:00+00:00", side=-1, kind="added", adx="18.4",
                              adx_3_bars_back="14.1", simulated=dict(status="donchian_exit", gross_return_pct=2.5))]),
    notifications=dict(pending=0, failing=1, recent=[]), jev_statuses=dict(called=4, skipped_no_setup=318),
    model_cost_24h_usd=0.0001, next_bar_close="2026-10-20T08:00:00+00:00",
    attention=["HYPEUSDT:spot heartbeat 0.0%"], status="attention")
CHART = dict(route="ZECUSDT:perp",
             bars=[dict(at=f"2026-10-{d:02d}T00:00:00+00:00", open=100.0+d, high=102.0+d, low=99.0+d, close=101.0+d,
                        volume=10.0) for d in range(1, 21)],
             upper=[110.0]*20, lower=[95.0]*20, ema50=[105.0]*20, ema200=[None]*20,
             marks=[dict(k=15, side=-1, close=116.0, stop=121.8, end=18, level=None, at="x")])


def text(renderable, width=200):
    console = Console(width=width, record=True, file=io.StringIO(), color_system=None)
    console.print(renderable)
    return console.export_text()


class FakeRemote(Remote):
    def __init__(self, responses, tunnel="existing"):
        super().__init__("julius@vps")
        self.responses, self.tunnel_state, self.paths = responses, tunnel, []

    def open_tunnel(self, **_):
        if isinstance(self.tunnel_state, Exception):
            raise self.tunnel_state
        return self.tunnel_state

    def get(self, path):
        self.paths.append(path)
        value = self.responses.get(path.split("?")[0] if path.startswith("/api/setup2/chart") else path)
        if isinstance(value, Exception) or value is None:
            raise value or RemoteError(f"{path}: HTTP 404")
        return value


def run_app(remote, check, size=(170, 50)):
    async def main():
        app = AigtApp(remote)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.pause(0.3)
            await check(app, pilot)
    asyncio.run(main())


def test_candles_draw_bodies_wicks_and_half_cells():
    payload = dict(route="X:perp", bars=[
        dict(at="2026-10-01T00:00:00+00:00", open=10.0, high=13.0, low=9.0, close=12.0, volume=1.0),
        dict(at="2026-10-01T04:00:00+00:00", open=12.0, high=12.5, low=10.5, close=11.0, volume=2.0)])
    lines = candles.render(payload, width=candles.AXIS+5, height=6).plain.split("\n")
    # four price rows over 8.92..13.08: the up bar is wick, two body rows, wick; the down bar ends in a top half
    assert [line[:2] for line in lines[:4]] == ["││", "██", "█▀", "│ "]
    assert lines[4][:2] == "▄█"                                       # volume 1 of 2, then 2 of 2
    assert candles.render(dict(bars=[]), 80, 20).plain == "Chưa đủ nến để vẽ."


def test_candles_show_the_entry_mark_and_its_stop_line_and_fit_the_width():
    chart = candles.render(CHART, width=candles.AXIS+20, height=20).plain
    assert "▼" in chart and chart.count("×") == 3                      # stop drawn from the bar after entry to `end`
    spaced = candles.render(CHART, width=candles.AXIS+20, height=20, gap=True).plain.split("\n")
    assert len(spaced[0]) <= candles.AXIS+20 and "×" in "".join(spaced)


def test_remote_quotes_the_remote_command_and_reports_a_dead_tunnel(monkeypatch):
    remote = Remote("julius@vps")
    assert remote.command(["learning", "proposals", "approve", "p-1", "--note", "ok; rm x"])[-1] == \
        "~/.local/bin/aigt learning proposals approve p-1 --note 'ok; rm x'"
    with pytest.raises(RemoteError, match="no host"):
        Remote(None).command(["status"])

    class Dead:
        stderr = io.BytesIO(b"Permission denied (publickey).")

        def poll(self):
            return 255
    monkeypatch.setattr(Remote, "healthy", lambda self: False)
    with pytest.raises(RemoteError, match="Permission denied"):
        remote.open_tunnel(wait=1, popen=lambda *a, **k: Dead())
    with pytest.raises(RemoteError, match="no host"):
        Remote(None).open_tunnel(wait=1)


def test_remote_run_streams_lines_and_the_exit_code():
    def popen(argv, **kwargs):
        return subprocess.Popen(["sh", "-c", "echo one; echo two >&2; exit 3"], **kwargs)
    assert list(Remote("julius@vps").run(["status"], popen=popen)) == ["one", "two", "[exit 3]"]


def test_app_fills_every_tab_from_the_api():
    remote = FakeRemote({"/api/setup2": SETUP2, "/api/dashboard": dict(
        status=dict(code="DEGRADED", label="Degraded", reasons=["jev_call_stale"]),
        soak=dict(evidence_version="scope-price-v2", persisted_evaluation=dict(status="pass", evaluated_at=None))),
        "/api/setup2/chart": CHART})

    async def check(app, pilot):
        overview = text(app.query_one("#overview-status").content)
        assert "đang chạy" in overview and "1 lỗi" in overview and "Dashboard BTC cũ" in overview
        assert "HYPEUSDT:spot heartbeat" in text(app.query_one("#overview-attention").content)
        bars = app.query_one("#overview-bars")
        assert [str(c) for c in bars.get_row_at(0)][:4] == ["ZEC Perp", "20/10 04:00Z", "1148.35", "có"]
        assert app.query_one("#setup2-pane").selected == "ZECUSDT:perp"   # the latest signal's route
        assert remote.paths[-1] == "/api/setup2/chart?route=ZECUSDT:perp"
        assert "▼" in candles.render(app.query_one("#setup2-chart").payload, 120, 30).plain
        signal = [str(c) for c in app.query_one("#setup2-signals").get_row_at(0)]
        assert signal[:4] == ["18/10 20:00Z", "ZEC Perp", "Short", "1055.48"] and "phản đối" in signal[4]
        assert "1/1 đã đóng · TB +2.50%" in text(app.query_one("#shadow-v4").content)
        assert [str(c) for c in app.query_one("#shadow-v4-items").get_row_at(0)][2] == "V4 vào, luật không"
        assert "khớp 4" in text(app.query_one("#shadow-limit").content)
        soak = text(app.query_one("#soak-routes").content)
        assert "HYPE Spot" in soak and "0.0%" in soak
        assert "2 đạt heartbeat" in text(app.query_one("#soak-summary").content)
        app.query_one("#setup2-routes").move_cursor(row=0)
        await pilot.pause(0.3)
        await app.workers.wait_for_complete()
        assert remote.paths[-1] == "/api/setup2/chart?route=ETHUSDT:perp"
    run_app(remote, check)


def test_app_survives_a_failing_endpoint_and_a_failed_tunnel():
    remote = FakeRemote({"/api/setup2": SETUP2, "/api/dashboard": RemoteError("/api/dashboard: timed out")})

    async def check(app, pilot):
        assert "dashboard: /api/dashboard: timed out" in str(app.query_one("#status").content)
        assert "Không tải được chart" in app.query_one("#setup2-chart").payload
        assert "đang chạy" in text(app.query_one("#overview-status").content)
    run_app(remote, check)

    down = FakeRemote({}, tunnel=RemoteError("ssh tunnel to julius@vps failed: refused"))

    async def check_down(app, pilot):
        assert "refused" in str(app.query_one("#status").content) and app.data["setup2"] is None
    run_app(down, check_down)


def test_ui_command_parses():
    arguments = _parser().parse_args(["ui", "--host", "julius@vps", "--port", "18082"])
    assert (arguments.command, arguments.host, arguments.port, arguments.no_tunnel) == ("ui", "julius@vps", 18082, False)
