"""Ctrl+P: every `aigt` command, with a form built from its arguments. Read commands run on the VPS at once,
write commands after the confirmation, terminal commands are only shown to copy (`commands.safety`)."""

from __future__ import annotations

import argparse
import json
import shlex
from functools import partial

from rich.json import JSON
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, RichLog, Select, Static

from intraday.tui import commands
from intraday.tui.remote import RemoteError

STYLE = {"read": "green", "write": "yellow", "terminal": "red"}
PRESET = {("logs", "no_follow"): True}       # a followed log would stream until the TUI closes


def parse_output(lines: list[str]):
    """The JSON a command printed, after the Docker progress lines that share its stream; `None` without one."""
    for start, line in enumerate(lines):
        if line.lstrip().startswith(("{", "[")):
            try:
                return lines[:start], json.loads("\n".join(lines[start:]))
            except ValueError:
                return None
    return None


def hint(action) -> str:
    text = "" if action.help in (None, argparse.SUPPRESS) else action.help
    if action.default not in (None, "", False, argparse.SUPPRESS) and not commands.is_flag(action):
        text = f"{text} (mặc định {action.default})".strip()
    return text


class AigtCommands(Provider):
    def _hit(self, spec):
        kind = commands.safety(spec)
        return f"aigt {spec.name}", partial(self.app.open_command, spec), \
            f"{commands.LABEL[kind]}" + (f" · {spec.help}" if spec.help else "")

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for spec in commands.specs():
            name, run, help_text = self._hit(spec)
            if (score := matcher.match(name)) > 0:
                yield Hit(score, matcher.highlight(name), run, text=name, help=help_text)

    async def discover(self) -> Hits:
        for spec in commands.specs():
            name, run, help_text = self._hit(spec)
            yield DiscoveryHit(name, run, text=name, help=help_text)


class CommandScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    CommandScreen { align: center middle; }
    CommandScreen > Vertical { width: 120; height: 90%; border: thick $accent; background: $surface; padding: 0 1; }
    CommandScreen #command-form { height: auto; max-height: 45%; }
    CommandScreen .field { height: auto; margin-top: 1; }
    CommandScreen .field Label { width: 28; padding-top: 1; }
    CommandScreen .field Input, CommandScreen .field Select { width: 1fr; }
    CommandScreen #command-preview { background: $panel; padding: 0 1; margin-top: 1; }
    CommandScreen #command-buttons { height: auto; align: right middle; margin-top: 1; }
    CommandScreen #command-buttons Button { margin-left: 2; }
    CommandScreen #command-output { height: 1fr; border: round $panel-lighten-2; margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "close", "Đóng")]

    def __init__(self, spec: commands.Spec):
        super().__init__()
        self.spec = spec
        self.fields = commands.fields(spec)

    def compose(self) -> ComposeResult:
        kind = commands.safety(self.spec)
        with Vertical():
            yield Static(Text.assemble((f"aigt {self.spec.name}", "bold"), "  ",
                                       (f"[{commands.LABEL[kind]}]", STYLE[kind])))
            if self.spec.help:
                yield Static(Text(self.spec.help, style="dim"))
            with VerticalScroll(id="command-form"):
                for k, action in enumerate(self.fields):
                    label = action.option_strings[-1] if action.option_strings else action.dest
                    with Horizontal(classes="field"):
                        yield Label(Text(label + (" *" if action.required or not action.option_strings else ""),
                                         style="bold" if action.required else ""))
                        preset = PRESET.get((self.spec.name, action.dest))
                        if commands.is_flag(action):
                            yield Checkbox(hint(action), value=bool(preset), id=f"field-{k}")
                        elif action.choices:
                            yield Select([(str(c), str(c)) for c in action.choices], id=f"field-{k}",
                                         value=str(action.default) if str(action.default) in {str(c) for c in action.choices}
                                         else Select.NULL)
                        else:
                            yield Input(placeholder=hint(action), id=f"field-{k}")
            yield Static(id="command-preview")
            with Horizontal(id="command-buttons"):
                yield Button("Đóng", id="command-close")
                if kind != "terminal":
                    yield Button("Chạy" if kind == "read" else "Chạy…", id="command-run",
                                 variant="success" if kind == "read" else "warning")
            yield RichLog(id="command-output", wrap=True, highlight=True, max_lines=5000)

    def on_mount(self) -> None:
        self.update_preview()

    def values(self) -> dict:
        found = {}
        for k, action in enumerate(self.fields):
            widget = self.query_one(f"#field-{k}")
            value = widget.value
            found[action.dest] = None if value is Select.NULL else value
        return found

    def argv(self) -> list[str]:
        return commands.build_argv(self.spec, self.values())

    def update_preview(self) -> None:
        preview = self.query_one("#command-preview", Static)
        try:
            argv = self.argv()
        except ValueError as error:
            preview.update(Text(f"Chưa đủ tham số: {error}", style="yellow"))
            return
        kind = commands.safety(self.spec, argv)
        try:
            command = shlex.join(self.app.remote.command(argv))
        except RemoteError:
            command = shlex.join(["aigt", *argv])
        if kind == "terminal":
            command = command.replace("ssh -o BatchMode=yes", "ssh -t", 1)
            preview.update(Text.assemble((commands.why_terminal(self.spec)+"\n", "red"), f"$ {command}"))
        else:
            preview.update(Text(f"$ {command}"))

    def on_input_changed(self) -> None:
        self.update_preview()

    def on_checkbox_changed(self) -> None:
        self.update_preview()

    def on_select_changed(self) -> None:
        self.update_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "command-close":
            self.dismiss(None)
            return
        if event.button.id != "command-run":
            return
        try:
            argv = self.argv()
        except ValueError as error:
            self.notify(f"Chưa đủ tham số: {error}", severity="warning")
            return
        kind = commands.safety(self.spec, argv)
        log = self.query_one("#command-output", RichLog)
        if kind == "read":
            self.app.run_remote(argv, log=log, render=self.render_output)
        elif kind == "write":
            self.app.confirm_and_run(f"Chạy aigt {self.spec.name} trên VPS?", argv, log=log,
                                     render=self.render_output)

    def render_output(self, lines: list[str]) -> None:
        """Redraw the finished output: Docker progress dimmed, then the JSON highlighted."""
        parsed = parse_output(lines[:-1])
        if parsed is None:
            return
        noise, payload = parsed
        log = self.query_one("#command-output", RichLog)
        log.clear()
        for line in noise:
            log.write(Text(line, style="dim"))
        log.write(JSON.from_data(payload, ensure_ascii=False))
        log.write(Text(lines[-1], style="green" if lines[-1] == "[exit 0]" else "red bold"))

    def action_close(self) -> None:
        self.dismiss(None)
