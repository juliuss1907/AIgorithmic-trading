"""Tab "Setup-2": the soak routes, the H4 chart of the highlighted route and every entry signal since the soak
started, with Jev's view, the hour score, the simulated outcome and the shadow limit entry."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Static

from intraday.tui import candles, fmt
from intraday.tui.remote import RemoteError
from intraday.tui.views import Pane, fill, table

STANCE = {"support": ("ủng hộ", "green"), "oppose": ("phản đối", "red"), "neutral": ("trung lập", "dim")}


class CandleChart(Widget):
    """`candles.render` sized to the widget; `gap` spaces the candles out."""

    DEFAULT_CSS = "CandleChart { height: 1fr; min-height: 12; }"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.payload: dict | str = "Chọn một route."
        self.gap = True

    def set(self, payload: dict | str) -> None:
        self.payload = payload
        self.refresh()

    def render(self):
        if isinstance(self.payload, str):
            return Text(self.payload, style="dim")
        return candles.render(self.payload, self.size.width, self.size.height, gap=self.gap)


def legend(payload: dict) -> Text:
    if not payload.get("bars"):
        return Text("")
    last = lambda key: next((v for v in reversed(payload.get(key) or []) if v is not None), None)
    price = lambda v: "–" if v is None else candles._price(v)
    return Text.assemble(
        (f"{fmt.route(payload['route'])}  ", "bold"), (f"đóng {price(payload['bars'][-1]['close'])}  ", ""),
        ("· EMA50 ", "yellow"), (f"{price(last('ema50'))}  ", ""), ("· EMA200 ", "magenta"),
        (f"{price(last('ema200'))}  ", ""), ("─ Donchian ", "grey50"),
        (f"{price(last('lower'))}–{price(last('upper'))}  ", ""), ("▼▲ vào lệnh  × stop", "dim"))


def signal_row(s: dict) -> tuple:
    jev = s.get("jev") or {}
    stance, style = STANCE.get(jev.get("stance"), (jev.get("stance") or "–", "dim"))
    simulated, limit = s.get("simulated") or {}, s.get("limit") or {}
    return (fmt.when(s["bar_close"]), fmt.route(s["route"]), fmt.side(s["side"]), fmt.price(s["close"]),
            Text(f"{jev.get('choice') or '–'} {jev['confidence']:.2f} {stance}" if jev.get("confidence") is not None
                 else s.get("jev_status") or "–", style=style),
            fmt.pct(s.get("hour_score_pct")),
            Text.assemble(f"{simulated.get('status') or '–'} ", fmt.pct(simulated.get("gross_return_pct"))),
            Text.assemble(f"{limit.get('status') or '–'} ", fmt.pct(limit.get("diff_pct"))))


class Setup2Pane(Pane):
    BINDINGS = [Binding("z", "toggle_gap", "Thu/giãn nến")]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.selected: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="setup2-top"):
            yield table("Route", "Heartbeat", "Đóng", id="setup2-routes")
            with Vertical(id="setup2-chart-box"):
                yield Static(id="setup2-legend")
                yield CandleChart(id="setup2-chart")
        yield Static("Tín hiệu vào lệnh từ khi soak bắt đầu", classes="heading")
        yield table("Nến đóng", "Route", "Chiều", "Giá", "Jev", "Khung giờ", "Mô phỏng", "Limit 0,25 ATR",
                    id="setup2-signals")

    def show(self, data: dict) -> None:
        setup2 = data.get("setup2")
        if setup2 is None:
            return
        closes = {b["route"]: b["close"] for b in setup2["latest_bars"]}
        routes = [c["route"] for c in setup2["campaigns"]]
        fill(self.query_one("#setup2-routes", DataTable), [
            (fmt.route(c["route"]), Text(f"{c['coverage_pct']:.0f}%", style="green" if c["healthy"] else "red"),
             fmt.price(closes.get(c["route"]))) for c in setup2["campaigns"]], keys=routes)
        fill(self.query_one("#setup2-signals", DataTable), [signal_row(s) for s in setup2["signals"]])
        if self.selected not in routes:
            self.selected = next((s["route"] for s in setup2["signals"]), routes[0] if routes else None)
        if self.selected is not None:
            self.query_one("#setup2-routes", DataTable).move_cursor(row=routes.index(self.selected))
            self.load_chart(self.selected)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "setup2-routes" and event.row_key.value not in (None, self.selected):
            self.selected = event.row_key.value
            self.query_one(CandleChart).set(f"Đang tải chart {fmt.route(self.selected)}…")
            self.load_chart(self.selected)

    @work(thread=True, exclusive=True, group="chart")
    def load_chart(self, route: str) -> None:
        try:
            payload = self.app.remote.get(f"/api/setup2/chart?route={route}")
        except RemoteError as error:
            payload = f"Không tải được chart: {error}"
        self.app.call_from_thread(self._set_chart, route, payload)

    def _set_chart(self, route: str, payload) -> None:
        if route != self.selected:
            return
        self.query_one(CandleChart).set(payload)
        self.query_one("#setup2-legend", Static).update(legend(payload) if isinstance(payload, dict) else "")

    def action_toggle_gap(self) -> None:
        chart = self.query_one(CandleChart)
        chart.gap = not chart.gap
        chart.refresh()
