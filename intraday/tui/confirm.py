"""The confirmation every write of `aigt ui` passes: it shows the exact command run on the VPS."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class ConfirmScreen(ModalScreen[str | None]):
    """Dismissed with the note (empty without a note field) when confirmed, `None` when cancelled."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical { width: 90; height: auto; border: thick $warning; background: $surface; padding: 1 2; }
    ConfirmScreen .command { background: $panel; padding: 0 1; margin: 1 0; }
    ConfirmScreen Horizontal { height: auto; align: right middle; }
    ConfirmScreen Button { margin-left: 2; }
    """
    BINDINGS = [Binding("escape", "cancel", "Huỷ")]

    def __init__(self, title: str, command: str, *, note: bool = False):
        super().__init__()
        self.title_text, self.command, self.note = title, command, note

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.title_text, style="bold"))
            yield Static(Text(f"$ {self.command}"), classes="command")
            if self.note:
                yield Input(placeholder="Ghi chú (tùy chọn)", id="confirm-note")
            with Horizontal():
                yield Button("Huỷ", id="cancel")
                yield Button("Chạy", id="run", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.dismiss(self.query_one("#confirm-note", Input).value.strip() if self.note else "")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
