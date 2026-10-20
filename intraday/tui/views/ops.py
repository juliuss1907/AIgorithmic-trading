"""Tab "Vận hành": scheduler and external sources, the containers' log, and the service actions — each one a
command run on the VPS after a confirmation."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, RichLog, Static

from intraday.tui import fmt
from intraday.tui.views import Pane, fill, table

# Button id → (label, aigt arguments, needs a confirmation).
ACTIONS = {
    "ops-logs": ("Tải 200 dòng log", ["logs", "--no-follow", "--tail", "200"], False),
    "ops-backup": ("Backup DB", ["backup", "create"], True),
    "ops-restart": ("Restart worker + web", ["restart"], True),
    "ops-start": ("Start", ["start"], True),
    "ops-stop": ("Stop", ["stop"], True),
}
STATUS_STYLE = {"success": "green", "error": "red bold", "running": "cyan", "healthy": "green"}


class OpsPane(Pane):
    def compose(self) -> ComposeResult:
        with Horizontal(id="ops-actions"):
            for button_id, (label, _, confirm) in ACTIONS.items():
                yield Button(label, id=button_id, variant="warning" if confirm else "primary")
        yield RichLog(id="ops-log", wrap=False, highlight=True, max_lines=2000)
        yield Static("Job gần nhất", classes="heading")
        yield table("Job", "Lịch", "Trạng thái", "Bắt đầu", "Mã lỗi", id="ops-scheduler")
        yield Static("Nguồn dữ liệu ngoài", classes="heading")
        yield table("Nguồn", "Trạng thái", "Nhận lần cuối", "Số quan sát", id="ops-sources")

    def show(self, data: dict) -> None:
        dashboard = data.get("dashboard")
        if dashboard is None:
            return
        now = self.now()
        runs = sorted(dashboard.get("scheduler") or [], key=lambda r: r.get("started_at") or "", reverse=True)
        fill(self.query_one("#ops-scheduler"), [
            (r["job_name"], fmt.when(r.get("scheduled_for")),
             Text(r["status"], style=STATUS_STYLE.get(r["status"], "")), fmt.ago(r.get("started_at"), now),
             r.get("error_code") or "–") for r in runs])
        fill(self.query_one("#ops-sources"), [
            (name, Text(s.get("status") or "–", style=STATUS_STYLE.get(s.get("status"), "yellow")),
             fmt.ago(s.get("last_received_at"), now), f"{s.get('total_observations') or 0:,}")
            for name, s in sorted((dashboard.get("external_sources") or {}).items())])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id not in ACTIONS:
            return
        label, arguments, confirm = ACTIONS[event.button.id]
        log = self.query_one("#ops-log", RichLog)
        if confirm:
            self.app.confirm_and_run(f"{label} trên VPS?", arguments, log=log)
        else:
            self.app.run_remote(arguments, log=log)
