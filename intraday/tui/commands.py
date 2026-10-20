"""Every `aigt` command for the command palette of `aigt ui`, read from the argparse tree, and how far the TUI may
run each one on the VPS.

- read: runs at once. Only commands that reach the live database: those `aigt` routes into the containers (the
  host keeps an old database under ~/.local/state), plus `backup verify` on a host file.
- write: runs after the confirmation that shows the exact command. Data, reports and service actions only.
- terminal: never run by the TUI, only shown to copy. Anything that trades on Demo, sets leverage, takes a key,
  changes a rule, campaign or the active set, or runs on the host's old database. Undeclared commands land here.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import cache

READ = frozenset({
    "status", "doctor", "positions", "logs",
    "assets list", "assets status", "assets readiness", "assets rules status", "assets rules automation status",
    "active-set show", "setup2 evaluate", "replay list", "replay show", "replay confidence status",
    "portfolio soak report", "provider list", "provider show", "backup verify",
})
WRITE = frozenset({
    "backup create", "start", "stop", "restart", "analysis", "learning review", "setup2 backfill", "assets scan",
    "portfolio soak evaluate", "replay run", "replay funding fetch", "replay study", "replay confidence review",
    "replay confidence dismiss", "assets rules replay", "assets rules evaluate", "provider test",
})
TRADING = ("execution", "perp", "connect", "provider add", "provider remove", "provider activate",
           "provider deactivate")
HIDDEN = frozenset({"ui"})
LABEL = {"read": "chỉ đọc", "write": "ghi, cần xác nhận", "terminal": "chạy trong terminal"}


@dataclass(frozen=True)
class Spec:
    path: tuple[str, ...]
    help: str | None
    actions: tuple[argparse.Action, ...]

    @property
    def name(self) -> str:
        return " ".join(self.path)


def _walk(parser: argparse.ArgumentParser, path: tuple[str, ...], helps: dict) -> list[Spec]:
    subparsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subparsers:
        actions = tuple(a for a in parser._actions if not isinstance(a, (argparse._HelpAction,
                                                                         argparse._VersionAction)))
        return [Spec(path, helps.get(path), actions)]
    action = subparsers[0]
    choice_helps = {c.dest: c.help for c in action._choices_actions}
    specs = []
    for name, child in action.choices.items():
        child_path = path+(name,)
        if " ".join(child_path) not in HIDDEN:
            specs += _walk(child, child_path, {**helps, child_path: choice_helps.get(name) or child.description})
    return specs


@cache
def specs() -> tuple[Spec, ...]:
    from intraday.__main__ import _parser
    return tuple(_walk(_parser(), (), {}))


def safety(spec: Spec, argv: list[str] | None = None) -> str:
    """`read`, `write` or `terminal` for the command and, where it depends on them, its arguments."""
    name = spec.name
    if name == "learning proposals":
        action = (argv or [])[len(spec.path):len(spec.path)+1]
        return "read" if action == ["list"] else "write"
    if any(name == t or name.startswith(t+" ") for t in TRADING):
        return "terminal"
    return "read" if name in READ else "write" if name in WRITE else "terminal"


def why_terminal(spec: Spec) -> str:
    if any(spec.name == t or spec.name.startswith(t+" ") for t in TRADING):
        return "Lệnh này đặt lệnh Demo, đổi đòn bẩy hoặc nhập key, nên TUI không chạy. Chạy trong terminal:"
    return ("Lệnh này đổi luật, campaign, active set hoặc chạy trên DB cũ của host, nên TUI không chạy. "
            "Nếu cần, chạy trong terminal:")


# Paths that make `aigt` run on the host instead of in a container (`__main__.main`): never offered by the form.
NATIVE_PATHS = frozenset({"database", "secrets_file", "report_dir", "execution_database"})


def fields(spec: Spec) -> list[argparse.Action]:
    """The arguments the palette form offers: all but the native paths, and `--output` of `learning`."""
    return [a for a in spec.actions if a.dest not in NATIVE_PATHS
            and not (spec.path[0] == "learning" and a.dest == "output")]


def is_flag(action: argparse.Action) -> bool:
    return isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction))


def build_argv(spec: Spec, values: dict[str, object]) -> list[str]:
    """`aigt` arguments from the form: `values` maps each action's dest to its text, or a bool for a flag.
    Raises ValueError naming the first required field left empty."""
    argv, options = list(spec.path), []
    for action in spec.actions:
        value = values.get(action.dest)
        if is_flag(action):
            if value:
                options.append(action.option_strings[-1])
            continue
        text = "" if value is None else str(value).strip()
        if not text:
            if action.required or (not action.option_strings and action.nargs not in ("?", "*")):
                raise ValueError(f"thiếu {action.option_strings[-1] if action.option_strings else action.dest}")
            continue
        items = text.split() if action.nargs in ("+", "*") else [text]
        if action.option_strings:
            options += [action.option_strings[-1], *items]
        else:
            argv += items
    return argv+options
