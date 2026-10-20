"""Tab "Vòng học": the weekly reviews and their proposals; `a` approves, `x` rejects, both after a confirmation."""

from __future__ import annotations

import getpass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Static

from intraday.tui import fmt
from intraday.tui.views import Pane, fill, table

STATUS = {"pending": ("chờ duyệt", "yellow bold"), "approved": ("đã duyệt", "green"), "rejected": ("từ chối", "dim")}
REVIEW = {"valid": "green", "review_invalid": "red", "facts_only": "dim"}
SECTION = {"B2": "Lệnh", "C2": "Thoát lệnh", "D2": "Bộ lọc", "E2": "Jev"}


def headline(payload: dict) -> str:
    """The proposal in one line: a B2–D2 title, or the Jev change `name: current → proposed`."""
    if "title" in payload:
        return f"[{payload.get('kind', '')}] {payload['title']}"
    if "proposed" in payload:
        return f"{payload.get('name', '')}: {payload.get('current', '')} → {payload['proposed']}"
    return str(payload)[:120]


class LearningPane(Pane):
    BINDINGS = [Binding("a", "decide('approve')", "Duyệt"), Binding("x", "decide('reject')", "Từ chối")]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.proposals: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static("Đề xuất (a: duyệt · x: từ chối)", classes="heading")
        yield table("Id", "Phần", "Trạng thái", "Nội dung", "Quyết lúc", id="learning-proposals")
        yield Static(id="learning-detail", classes="card")
        yield Static("Các lần review", classes="heading")
        yield table("Review", "Lúc", "Kết quả", "Lỗi kiểm tra", "Model", "Đề xuất", id="learning-reviews")

    def show(self, data: dict) -> None:
        learning = data.get("learning")
        if learning is None:
            return
        self.proposals = learning["proposals"]
        fill(self.query_one("#learning-proposals", DataTable), [
            (p["id"], SECTION.get(p["section"], p["section"]), Text(*STATUS.get(p["status"], (p["status"], ""))),
             headline(p["payload"]), fmt.when(p.get("decided_at"))) for p in self.proposals],
            keys=[p["id"] for p in self.proposals])
        fill(self.query_one("#learning-reviews", DataTable), [
            (r["review_id"], fmt.when(r["created_at"]), Text(r["status"], style=REVIEW.get(r["status"], "")),
             str(r["errors"]), r.get("model") or "–", str(len(r["proposals"]))) for r in learning["reviews"]])
        self.show_detail()
        if not self.proposals:
            self.query_one("#learning-detail", Static).update(Text(
                "Chưa có đề xuất nào. Vòng học hằng tuần chỉ được bật sau G6.", style="dim"))

    def selected(self) -> dict | None:
        widget = self.query_one("#learning-proposals", DataTable)
        if not self.proposals or widget.cursor_row >= len(self.proposals):
            return None
        return self.proposals[widget.cursor_row]

    def show_detail(self) -> None:
        proposal = self.selected()
        if proposal is None:
            return
        payload = proposal["payload"]
        lines = [Text(headline(payload), style="bold")]
        for key in ("detail", "expected_effect"):
            if payload.get(key):
                lines.append(Text(payload[key]))
        if payload.get("evidence"):
            lines.append(Text("Bằng chứng: " + ", ".join(payload["evidence"]), style="dim"))
        if proposal.get("note"):
            lines.append(Text(f"Ghi chú của {proposal.get('actor')}: {proposal['note']}", style="italic"))
        self.query_one("#learning-detail", Static).update(Text("\n").join(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "learning-proposals":
            self.show_detail()

    def action_decide(self, decision: str) -> None:
        proposal = self.selected()
        if proposal is None:
            return
        if proposal["status"] != "pending":
            self.notify(f"{proposal['id']} đã {STATUS[proposal['status']][0]}.", severity="warning")
            return
        arguments = ["learning", "proposals", decision, proposal["id"], "--actor", getpass.getuser()]
        title = f"{'Duyệt' if decision == 'approve' else 'Từ chối'} đề xuất {proposal['id']}?"
        self.app.confirm_and_run(title, arguments, note=True)
