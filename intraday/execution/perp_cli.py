"""Short Perp commands; dispatch before the paper worker/environment setup."""

import json
from pathlib import Path
import sys
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from prompt_toolkit.shortcuts import input_dialog, button_dialog
from prompt_toolkit.validation import Validator

from intraday.assets import ticker_symbol
from intraday.config import resolve_database_path
from intraday.execution.binance_demo import BinanceDemoAdapter, DemoCredentials, DemoTransport
from intraday.execution.connect import DEFAULT_SECRETS_FILE, DEFAULT_EXECUTION_DATABASE
from intraday.execution.contracts import ExecutionUnavailable
from intraday.execution.journal import ExecutionJournal
from intraday.execution.perp_control import PerpController
from intraday.execution.scoped_source import ScopedEvidenceSource


def _interactive():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("leverage changes require an interactive terminal")


def enter_leverage():
    _interactive()
    def valid(text):
        return text.isascii() and text.isdecimal() and 1 <= int(text) <= 10
    return input_dialog(title="Perp leverage", text="Leverage mong muốn (1–10x):",
        default="3", validator=Validator.from_callable(valid, error_message="Nhập số nguyên từ 1 đến 10.", move_cursor_to_end=True)).run()


def confirm_leverage(preview):
    _interactive()
    return button_dialog(title="Confirm leverage", text=(
        f"{preview['symbol']} · Binance Demo · Isolated\n"
        f"Leverage: {preview['previous']}x → {preview['target']}x\n"
        f"Tài khoản: {preview['account'].split(':')[-1][:6]}…\nKhông kích hoạt trading."),
        buttons=[("Cancel", False), ("Confirm", True)]).run()


def show_information(info):
    print(f"{info['symbol']} · Binance Demo · {info['market']}")
    print(f"AIGT: {info['aigt_state']} · {info['margin_mode']} · Leverage sàn: {info['actual_leverage']}x · Cấu hình: {info['configured_leverage']}x")
    print(f"Lệnh mở: {info['open_orders']}")
    if not info["positions"]:
        print("Không có vị thế.")
    for p in info["positions"]:
        owner = "AIGT" if p["managed_by_aigt"] else "ngoài AIGT / chưa được quản lý"
        print(f"{p['side']} {p['quantity']} · Entry {p['entry_price']} · Mark {p['mark_price']} · Unrealized PnL {p['unrealized_pnl']} USDT · {owner}")
    print("PnL theo mark price, chưa bao gồm phí/funding.")
    print("Nhận lúc: " + datetime.fromisoformat(info["observed_at"]).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat())
    if info["change_blockers"]:
        print("Chặn đổi leverage: " + "; ".join(info["change_blockers"]))
    for item in info["leverage_requests"]:
        print(f"Leverage request {item['id']}: {item['status']} → {item['target']}x; reconcile before retrying.")


def add_perp_parser(commands):
    parser = commands.add_parser("perp", help="read a selected Perp account or edit leverage interactively")
    parser.add_argument("symbol", type=ticker_symbol)
    parser.add_argument("-leverage", "--leverage", action="store_true")
    parser.add_argument("--source-database", type=Path, default=None)
    parser.add_argument("--execution-database", type=Path, default=DEFAULT_EXECUTION_DATABASE)
    parser.add_argument("--secrets-file", type=Path, default=DEFAULT_SECRETS_FILE)


def dispatch_perp(arguments):
    try:
        source = ScopedEvidenceSource(arguments.source_database or resolve_database_path(), symbol=arguments.symbol, market="perp")
        source.route()  # Refuse unselected routes before reading any credential.
        paths = {source.path, arguments.execution_database.resolve(), arguments.secrets_file.resolve()}
        if len(paths) != 3:
            raise ValueError("source, journal and credential paths must be separate")
        credentials = DemoCredentials.load(arguments.secrets_file)
        venue = BinanceDemoAdapter(DemoTransport(credentials), symbol=arguments.symbol, market_scoped=True)
        journal = ExecutionJournal(arguments.execution_database, read_only=True) if arguments.execution_database.exists() else None
        control = PerpController(source, venue, journal)
        if arguments.leverage:
            value = enter_leverage()
            if value is None:
                print("Cancelled; configuration unchanged.")
                return
            if type(value) is str and value.isascii() and value.isdecimal():
                value = int(value)
            preview = control.preview(value)
            if not confirm_leverage(preview):
                print("Cancelled; configuration unchanged.")
                return
            journal = ExecutionJournal(arguments.execution_database)
            venue = BinanceDemoAdapter(DemoTransport(credentials, settings_enabled=True), symbol=arguments.symbol, market_scoped=True)
            control = PerpController(source, venue, journal)
            item = control.request_change(preview, request_id=uuid.uuid4().hex)
            print(json.dumps(control.process(item["id"]), indent=2))
            return
        show_information(control.information())
    except KeyboardInterrupt:
        print("Cancelled; if already submitted, check request status before retrying.")
    except (OSError, ValueError, KeyError, TypeError, ExecutionUnavailable) as error:
        message = str(error) if isinstance(error, (ExecutionUnavailable, ValueError)) and not hasattr(error, "errors") else "Perp information unavailable; check the selected route and account configuration"
        raise SystemExit(message) from None
