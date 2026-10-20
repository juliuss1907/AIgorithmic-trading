"""Tab "Soak": heartbeat coverage and progress of every Setup-2 soak route towards its review date."""

from __future__ import annotations

from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from intraday.tui import fmt
from intraday.tui.views import Pane

COVERAGE_MIN = 95.0                          # the soak gate's heartbeat threshold (setup2_dashboard.COVERAGE_MIN)


class SoakPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static(id="soak-summary", classes="card")
        yield Static(id="soak-routes", classes="card")
        yield Static(id="soak-legacy", classes="note")

    def show(self, data: dict) -> None:
        setup2, dashboard = data.get("setup2"), data.get("dashboard")
        now = self.now()
        if setup2 is not None:
            campaigns = setup2["campaigns"]
            review = max((c["review_at"] for c in campaigns), default=None)
            healthy = sum(c["healthy"] for c in campaigns)
            self.query_one("#soak-summary", Static).update(Text.assemble(
                ("Soak Setup-2  ", "bold"), f"{len(campaigns)} route · ",
                (f"{healthy} đạt heartbeat ≥{COVERAGE_MIN:.0f}%", "green" if healthy == len(campaigns) else "yellow"),
                f" · review muộn nhất {fmt.when(review)} ({fmt.ago(review, now)})"))
            grid = Table(box=None, padding=(0, 2), show_edge=False, expand=True)
            for column in ("Route", "Bắt đầu", "Tiến độ", "", "Heartbeat", "Nến", "Jev lỗi", "Review"):
                grid.add_column(column, ratio=3 if column == "Tiến độ" else None)
            for c in campaigns:
                grid.add_row(fmt.route(c["route"]), fmt.when(c["started_at"]),
                             ProgressBar(total=100, completed=min(100.0, c["progress_pct"]), width=None),
                             f"{c['progress_pct']:.0f}%",
                             Text(f"{c['coverage_pct']:.1f}%", style="green" if c["healthy"] else "red bold"),
                             str(c["bars"]), Text(str(c["jev_errors"]), style="red" if c["jev_errors"] else "dim"),
                             fmt.when(c["review_at"]))
            self.query_one("#soak-routes", Static).update(grid)
        if dashboard is not None:
            persisted = dashboard["soak"].get("persisted_evaluation") or {}
            self.query_one("#soak-legacy", Static).update(Text(
                f"Soak BTC cũ ({dashboard['soak']['evidence_version']}): {persisted.get('status', '–')} "
                f"lúc {fmt.when(persisted.get('evaluated_at'))}", style="dim"))
