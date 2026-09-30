"""Explicit local execution commands; not routed into the existing soak worker."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from intraday.execution.binance_demo import BinanceDemoAdapter, DemoCredentials, DemoTransport
from intraday.execution.contracts import AccountRef, ExecutionUnavailable
from intraday.execution.journal import ExecutionJournal
from intraday.execution.runtime import DemoRuntime
from intraday.execution.source import EvidenceSource
from intraday.execution.connect import DEFAULT_EXECUTION_DATABASE, DEFAULT_SECRETS_FILE


def add_execution_parser(commands):
    execution = commands.add_parser("execution", help="isolated exchange execution modules")
    venues = execution.add_subparsers(dest="execution_venue", required=True)
    demo = venues.add_parser("demo", help="Binance Demo BTC USD-M Perp only")
    actions = demo.add_subparsers(dest="execution_action", required=True)
    for name in ("preflight", "status", "activate", "pause", "run", "flatten"):
        command = actions.add_parser(name)
        command.add_argument("--execution-database", type=Path, default=DEFAULT_EXECUTION_DATABASE)
        if name not in {"status", "pause"}:
            if name != "flatten":
                command.add_argument("--source-database", type=Path, required=True)
            command.add_argument("--secrets-file", type=Path, default=DEFAULT_SECRETS_FILE, help="owned 0600 Demo JSON secret file; never a key value")
        if name == "activate":
            command.add_argument("--evaluation-id", required=True)
            command.add_argument("--capital", type=Decimal, required=True, help="USDT strategy allocation, at most 10000")
        if name == "pause":
            command.add_argument("--account-id", help="local account fingerprint shown in status")
        if name == "run":
            command.add_argument("--once", action="store_true")
            command.add_argument("--interval-seconds", type=float, default=15)


def _print(payload):
    print(json.dumps(payload, indent=2), flush=True)


def dispatch_execution(arguments):
    """Keep orchestration here, before normal CLI environment/DB initialization."""
    action = arguments.execution_action
    now = datetime.now(timezone.utc)
    try:
        if action == "run" and not 10 <= arguments.interval_seconds <= 300:
            raise ValueError("execution interval must be between 10 and 300 seconds")
        if action not in {"status", "pause"}:
            source = EvidenceSource(arguments.source_database) if action != "flatten" else None
            if source is not None and source.path == arguments.execution_database.resolve():
                raise ValueError("execution database must be separate from the source/soak database")
            if (arguments.secrets_file.resolve() == arguments.execution_database.resolve()
                    or source is not None and arguments.secrets_file.resolve() == source.path):
                raise ValueError("credential file must be separate from databases")
            credentials = DemoCredentials.load(arguments.secrets_file)
        journal = ExecutionJournal(arguments.execution_database)
        if action == "status":
            _print(journal.status())
            return
        if action == "pause":
            with journal.lock():
                accounts = journal.status()["accounts"]
                if arguments.account_id:
                    key = AccountRef(venue="binance", environment="demo", account_id=arguments.account_id).key
                elif len(accounts) == 1:
                    key = next(iter(accounts))
                else:
                    raise ValueError("pause requires --account-id when no unique activated account exists")
                if key not in accounts:
                    raise ValueError("unknown activated Demo account")
                control = accounts[key]
                control.update(paused=True, reason="operator_pause")
                account = AccountRef(venue="binance", environment="demo", account_id=key.split(":", 2)[2])
                journal.save_control(account, control, kind="operator_pause", now=now)
                _print({"status": "paused", "account": key, "native_stops_retained": True})
            return
        control = journal.control(credentials.account_ref)
        writes = action in {"run", "flatten"} and control is not None and control.get("enabled") is True
        venue = BinanceDemoAdapter(DemoTransport(credentials, writes_enabled=writes))
        runner = DemoRuntime(journal, venue, source)
        if action == "preflight":
            _print(runner.preflight(now=now))
        elif action == "activate":
            _print(runner.activate(evaluation_id=arguments.evaluation_id, capital=arguments.capital, now=now))
        elif action == "flatten":
            _print(runner.flatten(now=now))
        else:
            while True:
                started = time.monotonic()
                _print(runner.cycle(now=datetime.now(timezone.utc)))
                if arguments.once:
                    break
                time.sleep(max(0, arguments.interval_seconds - (time.monotonic() - started)))
    except KeyboardInterrupt:
        # Native stops remain on the exchange. Stopping the process isn't flattening.
        return
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, ExecutionUnavailable) as error:
        # Do not print Pydantic validation input or raw third-party payloads here.
        if isinstance(error, (ExecutionUnavailable, ValueError)) and not hasattr(error, "errors"):
            message = str(error)
        else:
            message = "execution configuration or state unavailable; check paths, source schema, and preflight"
        raise SystemExit(message) from None
