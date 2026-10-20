"""Tab "Lệnh": the parent portfolio, its limits under the active rule profile, and the latest journal decisions."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from intraday.tui import fmt
from intraday.tui.views import Pane, fill, table

OUTCOME = {"ACCEPTED": "green", "REJECTED": "dim", "FILLED": "green bold"}


def money(value) -> str:
    return "–" if value is None else f"{value:,.2f}"


class TradesPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static(id="trades-portfolio", classes="card")
        yield Static("Quyết định gần đây trong journal", classes="heading")
        yield table("Lúc", "Coin", "Scope", "Hướng", "Độ tin", "Regime", "Kết quả", "Lý do", id="trades-signals")

    def show(self, data: dict) -> None:
        portfolio, signals = data.get("portfolio"), data.get("signals")
        if portfolio is not None:
            state, limits = portfolio.get("portfolio") or {}, portfolio.get("limits") or {}
            grid = Table.grid(padding=(0, 3))
            for _ in range(4):
                grid.add_column()
            paused = state.get("entries_paused")
            grid.add_row(Text("Vốn ban đầu", style="dim"), money(state.get("initial_equity")),
                         Text("Profile", style="dim"), limits.get("profile", "–"))
            grid.add_row(Text("Lãi đã chốt", style="dim"), money(state.get("realized_pnl")),
                         Text("Spot / Perp", style="dim"),
                         f"{limits.get('spot_budget_pct', 0):.0%} / {limits.get('perp_budget_pct', 0):.0%}")
            grid.add_row(Text("Phí · funding", style="dim"), f"{money(state.get('fees'))} · {money(state.get('funding'))}",
                         Text("Đòn bẩy", style="dim"), f"{limits.get('leverage', '–')}×")
            grid.add_row(Text("Spot · Perp đang giữ", style="dim"),
                         f"{state.get('spot_quantity', 0)} · {state.get('perp_quantity', 0)}",
                         Text("Lỗ ngày · DD tối đa", style="dim"),
                         f"{limits.get('daily_loss_limit_pct', 0):.0%} · {limits.get('max_drawdown_pct', 0):.0%}")
            grid.add_row(Text("Vào lệnh", style="dim"),
                         Text(f"tạm dừng ({state.get('halt_reason') or '–'})", style="yellow") if paused
                         else Text("mở", style="green"),
                         Text("Cập nhật", style="dim"), fmt.when(state.get("updated_at")))
            self.query_one("#trades-portfolio", Static).update(grid)
        if signals is not None:
            fill(self.query_one("#trades-signals"), [
                (fmt.when(s["timestamp"]), s["symbol"].removesuffix("USDT"), s["scope"], s.get("direction") or "–",
                 "–" if s.get("confidence") is None else f"{s['confidence']:.2f}", s.get("regime") or "–",
                 Text(s.get("outcome") or "–", style=OUTCOME.get(s.get("outcome"), "")), s.get("gate_reason") or "–")
                for s in signals])
