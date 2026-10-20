"""Tab "Tổng quan": is the worker alive, what needs attention, the soak routes and their latest H4 bar."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from intraday.tui import fmt
from intraday.tui.views import Pane, fill, table


class OverviewPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static(id="overview-status", classes="card")
        yield Static(id="overview-attention", classes="card")
        yield Static("Nến H4 mới nhất", classes="heading")
        yield table("Route", "Nến đóng", "Giá đóng", "Breakout", "Vào lệnh", "Bộ lọc trượt", "Jev",
                    id="overview-bars")
        yield Static("Lỗi job 24 giờ", classes="heading")
        yield table("Job", "Mã lỗi", "Số lần", "Gần nhất", id="overview-errors")

    def show(self, data: dict) -> None:
        setup2, dashboard = data.get("setup2"), data.get("dashboard")
        now = self.now()
        if setup2 is None:
            self.query_one("#overview-status", Static).update(Text("Đang tải /api/setup2…", style="dim"))
            return
        health, notes = setup2["health"], setup2["notifications"]
        grid = Table.grid(padding=(0, 3))
        grid.add_column(style="dim")
        grid.add_column()
        grid.add_row("Worker", Text.assemble(fmt.flag(not health["stale"], "đang chạy", "không chạy"),
                                             f"  job cuối {fmt.ago(health['last_run'], now)}"))
        grid.add_row("Nến H4 tiếp theo", f"{fmt.when(setup2['next_bar_close'])} ({fmt.ago(setup2['next_bar_close'], now)})")
        grid.add_row("Telegram", Text.assemble(f"{notes['pending']} chờ gửi · ",
                                               Text(f"{notes['failing']} lỗi", style="red bold" if notes["failing"] else "")))
        grid.add_row("Chi phí model 24h", f"${setup2['model_cost_24h_usd'] or 0:.4f}")
        grid.add_row("Jev", " · ".join(f"{k} {v}" for k, v in sorted(setup2["jev_statuses"].items())) or "–")
        if dashboard is not None:
            status = dashboard["status"]
            grid.add_row("Dashboard BTC cũ", Text(f"{status['label']}" + (f" ({', '.join(status['reasons'])})"
                                                                           if status["reasons"] else ""), style="dim"))
        self.query_one("#overview-status", Static).update(grid)
        attention = setup2["attention"]
        self.query_one("#overview-attention", Static).update(
            Group(Text("Cần chú ý", style="bold yellow"), *(Text(f"• {a}", style="yellow") for a in attention))
            if attention else Text("✓ Không có gì cần chú ý", style="bold green"))
        fill(self.query_one("#overview-bars"), [
            (fmt.route(b["route"]), fmt.when(b["bar_close"]), fmt.price(b["close"]),
             Text("có", style="cyan bold") if b["breakout"] else Text("–", style="dim"),
             Text("VÀO", style="green bold") if b["entry"] else Text("–", style="dim"),
             ", ".join(b["failed_filters"]+b["blockers"]) or "–", b.get("jev_status") or "–")
            for b in setup2["latest_bars"]])
        fill(self.query_one("#overview-errors"), [
            (e["job_name"], Text(e["error_code"] or "–", style="red"), str(e["n"]), fmt.when(e["last"]))
            for e in health["errors"]])
