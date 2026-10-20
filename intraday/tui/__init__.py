"""`aigt ui`: a full-screen terminal dashboard of the VPS, read from its web API through an SSH tunnel.

Textual is the optional `tui` extra, imported only when the command runs."""

from __future__ import annotations

INSTALL_HINT = "aigt ui cần Textual: uv tool install --editable '.[tui]' (hoặc uv sync --extra tui)"


def add_ui_parser(commands) -> None:
    ui = commands.add_parser("ui", help="full-screen dashboard of the VPS (optional extra: tui)")
    ui.add_argument("--host", default=None, help="ssh target of the VPS, e.g. user@host; saved for next time")
    ui.add_argument("--port", type=int, default=None, help="local tunnel port (default 18081)")
    ui.add_argument("--no-tunnel", action="store_true", help="read an already open tunnel, never start ssh")


def dispatch_ui(arguments) -> None:
    from intraday.tui.remote import DEFAULT_PORT, Remote, load_host, save_host
    try:
        from intraday.tui.app import AigtApp
    except ImportError as error:
        raise SystemExit(INSTALL_HINT) from error
    if arguments.host:
        save_host(arguments.host)
    host = None if arguments.no_tunnel else (arguments.host or load_host())
    AigtApp(Remote(host, arguments.port or DEFAULT_PORT)).run()
