"""`AigtApp`: the full-screen dashboard of `aigt ui`. Reads the VPS web API through `Remote` every minute."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from intraday.tui import fmt
from intraday.tui.remote import Remote, RemoteError
from intraday.tui.views import Pane
from intraday.tui.views.overview import OverviewPane
from intraday.tui.views.setup2 import Setup2Pane
from intraday.tui.views.shadow import ShadowPane
from intraday.tui.views.soak import SoakPane

REFRESH_SECONDS = 60
# Fetched in this order: the small Setup-2 snapshot first, so the main tabs fill before the slow overview.
ENDPOINTS = {"setup2": "/api/setup2", "dashboard": "/api/dashboard"}
TABS = (("Tổng quan", "overview", OverviewPane), ("Setup-2", "setup2", Setup2Pane),
        ("Theo dõi ngầm", "shadow", ShadowPane), ("Soak", "soak", SoakPane))


class AigtApp(App):
    TITLE = "AIGT"
    CSS = """
    Screen { background: $surface; }
    .heading { color: $accent; text-style: bold; margin: 1 0 0 1; }
    .card { border: round $panel-lighten-2; padding: 0 1; margin: 1 1 0 1; height: auto; }
    .note { margin: 1 1 0 2; }
    DataTable { height: auto; max-height: 24; margin: 0 1; }
    #setup2-top { height: 70%; min-height: 20; }
    #setup2-routes { width: 32; max-height: 100%; height: 100%; }
    #setup2-chart-box { border: round $panel-lighten-2; padding: 0 1; margin-right: 1; }
    #setup2-legend { height: 1; }
    #tabs { height: 1fr; }
    #status { height: 1; padding: 0 1; background: $panel; }
    """
    BINDINGS = [
        Binding("q", "quit", "Thoát"),
        Binding("r", "refresh", "Làm mới"),
        *(Binding(str(k+1), f"tab('{tab_id}')", title, show=False) for k, (title, tab_id, _) in enumerate(TABS)),
    ]

    def __init__(self, remote: Remote, *, refresh_seconds: float = REFRESH_SECONDS):
        super().__init__()
        self.remote = remote
        self.refresh_seconds = refresh_seconds
        self.data: dict = {name: None for name in ENDPOINTS}
        self.errors: dict[str, str] = {}
        self.fetched_at: datetime | None = None
        self.link = "đang kết nối…"
        self.sub_title = remote.host or remote.base_url

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            for title, tab_id, pane in TABS:
                with TabPane(title, id=tab_id):
                    yield pane(id=f"{tab_id}-pane")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.show_status()
        self.connect()
        self.set_interval(self.refresh_seconds, self.action_refresh)
        self.set_interval(1, self.show_status)

    def on_unmount(self) -> None:
        self.remote.close()

    @work(thread=True, exclusive=True, group="fetch")
    def connect(self) -> None:
        try:
            state = self.remote.open_tunnel()
        except RemoteError as error:
            self.call_from_thread(self.set_link, None, str(error))
            return
        self.call_from_thread(self.set_link, state, None)
        self._fetch()

    def set_link(self, state: str | None, error: str | None) -> None:
        self.link = error or ("tunnel ● " + ("đã mở" if state == "opened" else "có sẵn") + f" {self.remote.base_url}")
        if error:
            self.errors["tunnel"] = error
        else:
            self.errors.pop("tunnel", None)
        self.show_status()

    def action_refresh(self) -> None:
        if "tunnel" in self.errors:
            self.connect()
        else:
            self.fetch()

    @work(thread=True, exclusive=True, group="fetch")
    def fetch(self) -> None:
        self._fetch()

    def _fetch(self) -> None:
        for name, path in ENDPOINTS.items():
            try:
                payload, error = self.remote.get(path), None
            except RemoteError as failure:
                payload, error = None, str(failure)
            self.call_from_thread(self.apply, name, payload, error)

    def apply(self, name: str, payload, error: str | None) -> None:
        if error is None:
            self.data[name] = payload
            self.errors.pop(name, None)
            self.fetched_at = datetime.now(timezone.utc)
        else:
            self.errors[name] = error
        for pane in self.query(Pane):
            pane.show(self.data)
        self.show_status()

    def show_status(self) -> None:
        now = datetime.now(timezone.utc)
        setup2 = self.data.get("setup2")
        parts = [Text(self.link, style="red bold" if "tunnel" in self.errors else "green")]
        if self.fetched_at is not None:
            parts.append(Text(f"dữ liệu {fmt.ago(self.fetched_at.isoformat(), now)}"))
        if setup2 is not None:
            parts.append(Text(f"nến H4 tiếp {fmt.when(setup2['next_bar_close'])} "
                              f"({fmt.ago(setup2['next_bar_close'], now)})"))
        parts += [Text(f"{name}: {message}", style="red") for name, message in self.errors.items() if name != "tunnel"]
        self.query_one("#status", Static).update(Text("  ·  ", style="dim").join(parts))

    def action_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id
