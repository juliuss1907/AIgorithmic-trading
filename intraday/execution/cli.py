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
from intraday.assets import ticker_symbol
from intraday.execution.allocation import DemoAllocation
from intraday.execution.binance_spot_demo import BinanceSpotDemoAdapter
from intraday.execution.multi_runtime import MultiDemoRuntime
from intraday.execution.scoped_source import ScopedEvidenceSource


def add_execution_parser(commands):
    execution = commands.add_parser("execution", help="isolated exchange execution modules")
    venues = execution.add_subparsers(dest="execution_venue", required=True)
    demo = venues.add_parser("demo", help="Binance Demo; legacy BTC or explicit multi-route portfolio")
    actions = demo.add_subparsers(dest="execution_action", required=True)
    for name in ("preflight", "status", "activate", "pause", "run", "flatten", "control"):
        command = actions.add_parser(name)
        command.add_argument("--execution-database", type=Path, default=DEFAULT_EXECUTION_DATABASE)
        command.add_argument("--multi", action="store_true", help="shared multi-asset Spot/Perp portfolio (manual activation)")
        if name in {"preflight", "activate"}:
            command.add_argument("--symbol", type=ticker_symbol)
            command.add_argument("--market", choices=("spot", "perp"))
        if name not in {"status", "pause"}:
            if name != "flatten":
                command.add_argument("--source-database", type=Path, required=True)
            command.add_argument("--secrets-file", type=Path, default=DEFAULT_SECRETS_FILE, help="owned 0600 Demo JSON secret file; never a key value")
        if name == "activate":
            command.add_argument("--evaluation-id", required=True)
            command.add_argument("--capital", type=Decimal, help="legacy BTC capital; multi portfolio uses allocation configuration")
        if name == "pause":
            command.add_argument("--account-id", help="local account fingerprint shown in status")
        if name in {"run", "control"}:
            command.add_argument("--once", action="store_true")
            command.add_argument("--interval-seconds", type=float, default=15)
    configure = actions.add_parser("configure", help="set manual coin weights; portfolio stays paused")
    configure.add_argument("--execution-database", type=Path, default=DEFAULT_EXECUTION_DATABASE)
    configure.add_argument("--source-database", type=Path, required=True)
    configure.add_argument("--secrets-file", type=Path, default=DEFAULT_SECRETS_FILE)
    configure.add_argument("--capital", type=Decimal, required=True)
    configure.add_argument("--spot-stop-percent", type=Decimal, default=Decimal(10), help="Spot emergency stop below entry; operator-selected default 10 percent")
    configure.add_argument("--spot-weight", action="append", default=[], metavar="COIN=FRACTION")
    configure.add_argument("--perp-weight", action="append", default=[], metavar="COIN=FRACTION")
    configure.set_defaults(multi=True)


def _print(payload):
    print(json.dumps(payload, indent=2), flush=True)


def _weights(values):
    result = {}
    for item in values:
        symbol, separator, weight = item.partition("=")
        symbol = ticker_symbol(symbol)
        if not separator or symbol in result:
            raise ValueError("weights require unique COIN=FRACTION entries")
        result[symbol] = Decimal(weight)
    return result


def _dispatch_multi(arguments,journal,credentials,source,*,now):
    action = arguments.execution_action
    portfolio = journal.portfolio(credentials.account_ref)
    writes = action in {"run","flatten"} and portfolio is not None and portfolio.get("enabled") is True
    venues = {}
    def venue_factory(symbol,market):
        if (symbol,market) in venues:
            return venues[(symbol,market)]
        transport = DemoTransport(credentials,market=market,writes_enabled=writes)
        venue = (BinanceSpotDemoAdapter(transport,symbol=symbol) if market=="spot" else
                 BinanceDemoAdapter(transport,symbol=symbol,market_scoped=True))
        venues[(symbol,market)] = venue
        return venue
    def source_factory(symbol,market):
        if source is None:
            raise ValueError("read-only source required")
        return ScopedEvidenceSource(source.path,symbol=symbol,market=market)
    runner = MultiDemoRuntime(journal,credentials.account_ref,venue_factory,source_factory)
    if action=="configure":
        _print(runner.configure(DemoAllocation(capital=arguments.capital,spot_weights=_weights(arguments.spot_weight),
                                              perp_weights=_weights(arguments.perp_weight),
                                              spot_emergency_stop_pct=arguments.spot_stop_percent/100),now=now))
    elif action in {"preflight","activate"}:
        if not arguments.symbol or not arguments.market:
            raise ValueError("multi preflight/activate requires --symbol and --market")
        if action=="preflight":
            _print(runner.preflight(arguments.symbol,arguments.market,now=now))
        else:
            if arguments.capital is not None:
                raise ValueError("configure multi capital/weights separately; activation cannot reset allocation")
            _print(runner.activate(arguments.symbol,arguments.market,evaluation_id=arguments.evaluation_id,now=now))
    elif action=="flatten":
        _print(runner.flatten(now=now))
    else:
        while True:
            started = time.monotonic()
            _print(runner.cycle(now=datetime.now(timezone.utc)))
            if arguments.once:
                break
            time.sleep(max(0,arguments.interval_seconds-(time.monotonic()-started)))


def dispatch_execution(arguments):
    """Keep orchestration here, before normal CLI environment/DB initialization."""
    action = arguments.execution_action
    now = datetime.now(timezone.utc)
    try:
        if action in {"run", "control"} and not 10 <= arguments.interval_seconds <= 300:
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
        if action == "control":
            from intraday.execution.control_worker import run_control_cycle
            while True:
                started = time.monotonic()
                _print(run_control_cycle(source, journal, credentials))
                if arguments.once:
                    return
                time.sleep(max(0, arguments.interval_seconds-(time.monotonic()-started)))
        if action == "status":
            _print(journal.status())
            return
        if action == "pause":
            with journal.lock():
                if arguments.multi:
                    portfolios = journal.status()["portfolios"]
                    if arguments.account_id:
                        key = AccountRef(venue="binance",environment="demo",account_id=arguments.account_id).key
                    elif len(portfolios)==1:
                        key = next(iter(portfolios))
                    else:
                        raise ValueError("multi pause requires --account-id when there is no unique portfolio")
                    if key not in portfolios:
                        raise ValueError("unknown multi-route portfolio")
                    p = portfolios[key];p.update(paused=True,reason="operator_pause")
                    journal.save_portfolio(AccountRef.from_key(key),p,now=now,kind="operator_pause")
                    _print({"status":"paused","native_stops_retained":True})
                    return
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
                account = AccountRef.from_key(key)
                journal.save_control(account, control, kind="operator_pause", now=now)
                _print({"status": "paused", "account": key, "native_stops_retained": True})
            return
        if arguments.multi:
            return _dispatch_multi(arguments,journal,credentials,source,now=now)
        if action in {"preflight","activate"} and (arguments.symbol is not None or arguments.market is not None):
            raise ValueError("symbol/market options require --multi; legacy mode is BTC Perp only")
        if action=="activate" and arguments.capital is None:
            raise ValueError("legacy BTC activation requires --capital")
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
