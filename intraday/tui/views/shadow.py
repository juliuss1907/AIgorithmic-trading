"""Tab "Theo dõi ngầm": variants the soak tracks without trading them — V4 (ADX rising over three bars), the
limit entry 0.25 ATR above the signal close, and the hour score."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from intraday.tui import fmt
from intraday.tui.views import Pane, fill, table

KIND = {"added": ("V4 vào, luật không", "cyan"), "dropped": ("Luật vào, V4 bỏ", "yellow"),
        "no_data": ("Thiếu ADX", "dim")}
EXIT = {"stop": "Chạm stop", "donchian_exit": "Thoát kênh", "open": "Đang mở"}
HOURS = (("with_hours", "Cùng chiều khung giờ"), ("against_hours", "Ngược chiều khung giờ"),
         ("unscored", "Chưa đủ dữ liệu khung giờ"))


def number(value) -> str:
    return "–" if value is None else f"{float(value):.1f}"


class ShadowPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static("Biến thể V4: ADX tăng so với 3 nến trước", classes="heading")
        yield Static(id="shadow-v4", classes="card")
        yield table("Nến đóng", "Route", "V4 so với luật", "ADX", "ADX 3 nến trước", "Mô phỏng", id="shadow-v4-items")
        yield Static("Vào lệnh limit 0,25 ATR (Perp short)", classes="heading")
        yield Static(id="shadow-limit", classes="card")
        yield Static("Điểm khung giờ", classes="heading")
        yield Static(id="shadow-hours", classes="card")

    def show(self, data: dict) -> None:
        setup2 = data.get("setup2")
        if setup2 is None:
            return
        rise = setup2.get("adx_rise")
        if rise is None:
            self.query_one("#shadow-v4", Static).update(Text("Chưa có route nào trong soak.", style="dim"))
        else:
            summary = rise["summary"]
            self.query_one("#shadow-v4", Static).update(Text.assemble(
                ("Thêm ", "cyan bold"), fmt.stat(summary["added"]), "   ", ("Bỏ ", "yellow bold"),
                fmt.stat(summary["dropped"]), "   ", (f"Thiếu ADX {summary['no_data']}", "dim"),
                ("" if rise["items"] else "\nChưa có tín hiệu nào mà V4 khác luật.", "dim")))
            fill(self.query_one("#shadow-v4-items"), [
                (fmt.when(i["bar_close"]), fmt.route(f"{i['symbol']}:{i['market']}"), Text(*KIND.get(i["kind"], (i["kind"], ""))),
                 number(i.get("adx")), number(i.get("adx_3_bars_back")),
                 Text.assemble(f"{EXIT.get((i.get('simulated') or {}).get('status'), (i.get('simulated') or {}).get('status') or '–')} ",
                               fmt.pct((i.get("simulated") or {}).get("gross_return_pct"))))
                for i in rise["items"]])
        limit = setup2.get("limit_entry")
        self.query_one("#shadow-limit", Static).update(Text("Chưa có tín hiệu.", style="dim") if not limit else Text.assemble(
            f"{limit['signals']} tín hiệu · khớp {limit['filled']} · không khớp {limit['unfilled']} · "
            f"chờ {limit['pending']} · thiếu dữ liệu {limit['no_data']}\n",
            f"Tốt hơn giá thị trường {limit['better']}/{limit['n']} · chênh TB ", fmt.pct(limit.get("mean_return_pct")),
            f" (SE {limit['standard_error_pct']:.2f}, t {limit['t_stat']:.2f})" if limit.get("t_stat") is not None else ""))
        hours = setup2.get("hour_score")
        grid = Table(box=None, padding=(0, 2), show_edge=False)
        for column in ("Nhóm", "Tín hiệu", "Thắng", "Đã đóng", "TB", "SE"):
            grid.add_column(column, style="dim" if column == "Nhóm" else "")
        for key, label in HOURS:
            s = (hours or {}).get(key) or {}
            grid.add_row(label, str(s.get("signals", 0)), str(s.get("wins", 0)), str(s.get("n", 0)),
                         fmt.pct(s.get("mean_return_pct")),
                         "–" if s.get("standard_error_pct") is None else f"{s['standard_error_pct']:.2f}")
        self.query_one("#shadow-hours", Static).update(grid)
