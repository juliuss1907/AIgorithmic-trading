"""Local credential wizard. Connection never activates an execution campaign."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from prompt_toolkit import prompt

from intraday.execution.binance_demo import BinanceDemoAdapter, DemoCredentials, DemoTransport
from intraday.execution.binance_spot_demo import BinanceSpotDemoAdapter
from intraday.execution.contracts import ExecutionUnavailable
from intraday.execution.credential_file import ConnectError, credential_lock, rotation_guard, save_credentials


DEFAULT_SECRETS_FILE = Path("state/execution-secrets/binance-demo.json")
DEFAULT_EXECUTION_DATABASE = Path("state/execution/binance-demo.sqlite3")


def add_exchange_connect_parsers(commands):
    for venue in ("bnb", "hl"):
        command = commands.add_parser(venue, help="exchange credentials; only bnb demo is supported")
        command.add_argument("connect_environment", nargs="?", choices=("demo", "live"), default="live")
        command.add_argument("--secrets-file", type=Path, default=DEFAULT_SECRETS_FILE)
        command.add_argument("--execution-database", type=Path, default=DEFAULT_EXECUTION_DATABASE)


def _prompt(message, *, password=False):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ConnectError("connect requires an interactive terminal; secret echo fallback is disabled")
    return prompt(message, is_password=password)


def _credentials():
    values = {
        "api_key": _prompt("Binance Demo API key: ", password=True),
        "api_secret": _prompt("Binance Demo API secret: ", password=True),
    }
    if any(not value or len(value) > 1024 or not value.isascii()
           or not value.isprintable() or any(char.isspace() for char in value)
           for value in values.values()):
        raise ConnectError("invalid credential input; expected nonempty API key and secret without whitespace")
    return DemoCredentials(**values)


def _probe_perp(credentials):
    venue = BinanceDemoAdapter(DemoTransport(credentials, writes_enabled=False))
    venue.check_clock(now=datetime.now(timezone.utc))
    snapshot = venue.account_snapshot(now=datetime.now(timezone.utc))
    warnings = []
    if not snapshot.can_trade:
        warnings.append("account trading is disabled")
    if not snapshot.one_way:
        warnings.append("One-way mode required")
    if not snapshot.single_asset:
        warnings.append("Single-asset mode required")
    if snapshot.margin_mode != "ISOLATED" or snapshot.leverage != 3:
        warnings.append("BTCUSDT isolated 3x required; change settings manually")
    return {
        "status": "connected", "venue": "binance", "environment": "demo",
        "account": snapshot.account.key, "wallet_balance": str(snapshot.wallet_balance),
        "available_balance": str(snapshot.available_balance),
        "positions": len(snapshot.positions), "open_orders": len(snapshot.open_orders),
        "account_can_trade": snapshot.can_trade,
        "order_permission_verified": False, "trading_activated_by_connect": False,
        "one_way": snapshot.one_way, "single_asset": snapshot.single_asset,
        "margin_mode": snapshot.margin_mode, "leverage": snapshot.leverage,
        "configuration_warnings": warnings, "observed_at": snapshot.observed_at.isoformat(),
    }


def probe_demo(credentials):
    """Probe both markets independently; connecting never enables order writes."""
    try:
        report = _probe_perp(credentials)
        perp = dict(report)
    except (ExecutionUnavailable, ValueError, TypeError, KeyError, StopIteration):
        perp = {"status": "unavailable", "reason": "perp_account_read_unavailable"}
        # Fail closed for rotation: an unreadable Perp account is not proof of flatness.
        report = {"status": "unavailable", "account": credentials.account_ref.key,
                  "venue": "binance", "environment": "demo", "positions": None, "open_orders": None,
                  "order_permission_verified": False, "trading_activated_by_connect": False}
    try:
        venue = BinanceSpotDemoAdapter(DemoTransport(credentials, market="spot", writes_enabled=False), symbol="BTCUSDT")
        venue.check_clock(now=datetime.now(timezone.utc))
        snapshot = venue.account_snapshot(now=datetime.now(timezone.utc))
        spot = {"status": "connected", "account": snapshot.account.key,
                "account_can_trade": snapshot.can_trade, "open_orders": len(snapshot.open_orders),
                "available_usdt": str(snapshot.balance("USDT").free),
                "pre_existing_inventory": "excluded_from_strategy", "order_permission_verified": False}
        if report["open_orders"] is not None:
            report["open_orders"] += len(snapshot.open_orders)
    except (ExecutionUnavailable, ValueError, TypeError, KeyError):
        spot = {"status": "unavailable", "reason": "spot_account_read_unavailable"}
        # Replacement cannot be approved while another market's orders are unknown.
        report["open_orders"] = None
    report["markets"] = {"spot": spot, "perp": perp}
    if not any(m["status"] == "connected" for m in (spot, perp)):
        raise ExecutionUnavailable("Demo account read unavailable for both markets")
    report["status"] = "connected"
    return report


def dispatch_connect(arguments):
    if arguments.connect_role != "bnb" or arguments.connect_environment != "demo":
        raise SystemExit("exchange connection not supported yet; supported command: aigt connect bnb demo")
    try:
        # Storage and replacement guards are kept out of the exchange adapter.
        path = arguments.secrets_file.absolute()
        if path.resolve() == arguments.execution_database.resolve():
            raise ConnectError("credential file must be separate from the execution database")
        with credential_lock(path):
            existing = DemoCredentials.load(path) if path.exists() else None
            if existing is not None:
                key = existing.api_key.get_secret_value()
                hint = key[:4] + "..." if len(key) > 4 and key[:4].isalnum() else "****"
                print(f"Binance Demo API key: {hint} ✓", flush=True)
                choice = _prompt("  [K]eep / [R]eplace / [C]lear (default K): ").strip().upper() or "K"
                if choice not in {"K", "R", "C"}:
                    raise ConnectError("choose K, R, or C; credentials unchanged")
            else:
                choice = "R"
            if choice == "K":
                report = probe_demo(existing)
            elif existing is not None:
                with rotation_guard(arguments.execution_database, existing, probe=probe_demo):
                    report = _change(path, existing, choice)
            else:
                credentials = _credentials()
                report = probe_demo(credentials)
                save_credentials(path, credentials, replace=False)
            report["secrets_file"] = str(path)
            print(json.dumps(report, indent=2), flush=True)
            return report
    except (KeyboardInterrupt, EOFError):
        raise SystemExit("connection cancelled; credentials unchanged") from None
    except ConnectError as error:
        raise SystemExit(str(error)) from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, ExecutionUnavailable):
        raise SystemExit("Demo connection failed; check credential file, API permissions, clock, and network; credentials unchanged") from None


def _change(path, existing, choice):
    if _prompt("Replace stored key? [y/N]: " if choice == "R" else "Clear stored key file (key remains on Binance)? [y/N]: ").strip().lower() != "y":
        return {"status": "cancelled", "credentials_changed": False}
    if DemoCredentials.load(path) != existing:
        raise ConnectError("credential file changed during connection; change refused")
    if choice == "C":
        path.unlink()
        return {"status": "cleared", "binance_key_revoked": False, "journal_retained": True}
    credentials = _credentials()
    report = probe_demo(credentials)
    # Existing credentials and campaign are retained unless every check succeeds.
    if DemoCredentials.load(path) != existing:
        raise ConnectError("credential file changed during connection; replacement refused")
    save_credentials(path, credentials, replace=True)
    report["reactivation_required"] = True
    return report
