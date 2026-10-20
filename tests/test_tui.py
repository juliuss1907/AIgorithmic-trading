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
        self.responses, self.tunnel_state, self.paths, self.ran = responses, tunnel, [], []

    output = ("done",)

    def run(self, arguments, **_):
        self.ran.append(arguments)
        yield from self.output
        yield "[exit 0]"

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


PROPOSAL = dict(id="lr-1-D2-1", review_id="lr-1", section="D2", status="pending", created_at="2026-10-19T02:31:00+00:00",
                decided_at=None, actor=None, note=None,
                payload=dict(kind="watch", title="volume_ma chặn breakout tốt", detail="Ba lần đều lãi.", evidence=["f1"]))
LEARNING = dict(reviews=[dict(review_id="lr-1", created_at="2026-10-19T02:30:00+00:00", status="valid", errors=0,
                              model="opus", proposals=["lr-1-D2-1"])],
                proposals=[PROPOSAL, dict(PROPOSAL, id="lr-1-B2-1", section="B2", status="approved")])
PORTFOLIO = dict(portfolio=dict(initial_equity=10000.0, realized_pnl=12.5, fees=1.0, funding=0.0, spot_quantity=0.0,
                                perp_quantity=0.0, entries_paused=True, halt_reason="soak_not_promoted",
                                updated_at="2026-09-24T13:07:50Z"),
                 limits=dict(profile="setup2_slots_v1", spot_budget_pct=0.5, perp_budget_pct=0.5, leverage=1,
                             daily_loss_limit_pct=0.03, max_drawdown_pct=0.15))
SIGNALS = [dict(timestamp="2026-10-18T20:00:04+00:00", symbol="ZECUSDT", scope="perp_intraday", direction="Buy",
                confidence=0.36, regime="Trending Down", outcome="REJECTED", gate_reason="setup2_soak_observation_only")]
DASHBOARD = dict(status=dict(code="OK", label="OK", reasons=[]),
                 soak=dict(evidence_version="v2", persisted_evaluation=None),
                 scheduler=[dict(job_name="asset_spot_4h_hypeusdt", scheduled_for="2026-10-20T04:00:00+00:00",
                                 status="error", started_at="2026-10-20T04:00:05+00:00", error_code="StaleMarketData")],
                 external_sources=dict(aster=dict(status="healthy", last_received_at="2026-10-20T04:00:00+00:00",
                                                  total_observations=170268)))


def full_remote():
    return FakeRemote({"/api/setup2": SETUP2, "/api/dashboard": DASHBOARD, "/api/setup2/chart": CHART,
                       "/api/learning": LEARNING, "/api/portfolio": PORTFOLIO, "/api/signals?limit=50": SIGNALS})


def test_trades_and_ops_tabs_show_the_portfolio_journal_scheduler_and_sources():
    async def check(app, pilot):
        portfolio = text(app.query_one("#trades-portfolio").content)
        assert "setup2_slots_v1" in portfolio and "tạm dừng (soak_not_promoted)" in portfolio and "12.50" in portfolio
        assert [str(c) for c in app.query_one("#trades-signals").get_row_at(0)][1:3] == ["ZEC", "perp_intraday"]
        assert [str(c) for c in app.query_one("#ops-scheduler").get_row_at(0)][4] == "StaleMarketData"
        assert [str(c) for c in app.query_one("#ops-sources").get_row_at(0)][3] == "170,268"
    run_app(full_remote(), check)


def test_learning_decisions_run_on_the_vps_only_after_the_confirmation():
    from intraday.tui.confirm import ConfirmScreen
    remote = full_remote()

    async def check(app, pilot):
        app.action_tab("learning")
        app.query_one("#learning-proposals").focus()
        await pilot.pause(0.2)
        assert "Ba lần đều lãi." in text(app.query_one("#learning-detail").content)
        await pilot.press("a")
        await pilot.pause(0.2)
        assert isinstance(app.screen, ConfirmScreen)
        assert "learning proposals approve lr-1-D2-1 --actor" in app.screen.command
        await pilot.press("escape")                                       # cancelled: nothing runs
        await pilot.pause(0.2)
        assert remote.ran == []
        await pilot.press("x")
        await pilot.pause(0.2)
        app.screen.query_one("#confirm-note").value = "mẫu nhỏ"
        await pilot.click("#run")
        await pilot.pause(0.3)
        await app.workers.wait_for_complete()
        [arguments] = remote.ran
        assert arguments[:4] == ["learning", "proposals", "reject", "lr-1-D2-1"] and arguments[-2:] == ["--note", "mẫu nhỏ"]
        app.query_one("#learning-proposals").move_cursor(row=1)        # already approved: no confirmation
        await pilot.press("a")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ConfirmScreen) and len(remote.ran) == 1
    run_app(remote, check)


def test_ops_logs_run_at_once_and_service_actions_wait_for_the_confirmation():
    from intraday.tui.confirm import ConfirmScreen
    remote = full_remote()

    async def check(app, pilot):
        app.action_tab("ops")
        await pilot.pause(0.2)
        await pilot.click("#ops-logs")
        await pilot.pause(0.3)
        await app.workers.wait_for_complete()
        assert remote.ran == [["logs", "--no-follow", "--tail", "200"]]
        assert any("done" in str(line) for line in app.query_one("#ops-log").lines)
        await pilot.click("#ops-restart")
        await pilot.pause(0.2)
        assert isinstance(app.screen, ConfirmScreen) and app.screen.command.endswith("aigt restart'")
        await pilot.click("#cancel")
        await pilot.pause(0.2)
        assert len(remote.ran) == 1
    run_app(remote, check)


def spec(name):
    from intraday.tui import commands
    return next(s for s in commands.specs() if s.name == name)


def test_every_command_has_a_safety_and_nothing_that_trades_or_takes_a_key_runs_from_the_tui():
    from intraday.tui import commands
    specs = commands.specs()
    names = {s.name for s in specs}
    assert "ui" not in names and {"execution demo run", "perp", "connect llm", "backup verify"} <= names
    assert commands.READ | commands.WRITE <= names                        # no stale declaration
    for s in specs:
        kind = commands.safety(s)
        assert kind in commands.LABEL
        if s.path[0] in ("execution", "perp", "connect") or s.name in ("provider add", "provider activate"):
            assert kind == "terminal", s.name
    for name in ("active-set switch", "setup2 candidate", "assets rules activate", "portfolio flatten",
                 "journal export"):
        assert commands.safety(spec(name)) == "terminal"
    proposals = spec("learning proposals")
    assert commands.safety(proposals, ["learning", "proposals", "list"]) == "read"
    assert commands.safety(proposals, ["learning", "proposals", "approve", "p-1"]) == "write"


def test_build_argv_puts_positionals_first_and_rejects_a_missing_required_field():
    from intraday.tui import commands
    readiness = spec("assets readiness")
    assert commands.build_argv(readiness, dict(symbol="ETHUSDT", market="perp", json_output=True)) == [
        "assets", "readiness", "ETHUSDT", "--market", "perp", "--json"]
    assert commands.build_argv(readiness, {}) == ["assets", "readiness"]          # symbol is optional here
    with pytest.raises(ValueError, match="backup_path"):
        commands.build_argv(spec("backup verify"), {})
    with pytest.raises(ValueError, match="--symbol"):
        commands.build_argv(spec("setup2 evaluate"), dict(market="perp"))


def test_parse_output_separates_docker_progress_from_the_json():
    from intraday.tui.palette import parse_output
    assert parse_output([" Container x Creating", "{", '  "a": 1', "}"]) == ([" Container x Creating"], {"a": 1})
    assert parse_output(["plain text"]) is None


def test_palette_form_runs_read_commands_at_once_and_only_shows_terminal_commands():
    from intraday.tui.confirm import ConfirmScreen
    from intraday.tui.palette import CommandScreen
    remote = full_remote()
    remote.output = (" Container intraday-admin-run Creating", '{"status": "ok", "symbol": "ETHUSDT"}')

    async def check(app, pilot):
        app.open_command(spec("assets readiness"))
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, CommandScreen)
        assert [a.dest for a in screen.fields] == ["symbol", "market", "json_output"]   # no --database
        screen.query_one("#field-0").value = "ETHUSDT"
        await pilot.pause(0.1)
        assert "aigt assets readiness ETHUSDT" in str(screen.query_one("#command-preview").content)
        await pilot.click("#command-run")
        await pilot.pause(0.3)
        await app.workers.wait_for_complete()
        await pilot.pause(0.2)
        assert remote.ran == [["assets", "readiness", "ETHUSDT"]]
        output = "\n".join(str(line.text) for line in screen.query_one("#command-output").lines)
        assert '"symbol": "ETHUSDT"' in output
        await pilot.press("escape")
        app.open_command(spec("execution demo flatten"))
        await pilot.pause(0.3)
        assert not app.screen.query("#command-run")
        assert "ssh -t julius@vps" in str(app.screen.query_one("#command-preview").content)
        await pilot.press("escape")
        app.open_command(spec("backup create"))
        await pilot.pause(0.3)
        await pilot.click("#command-run")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ConfirmScreen) and len(remote.ran) == 1
    run_app(remote, check)
