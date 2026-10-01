"""Opt-in settings controller. Order writes are never enabled here."""

from datetime import datetime, timezone
import sqlite3

from intraday.execution.binance_demo import BinanceDemoAdapter, DemoTransport
from intraday.execution.contracts import ExecutionUnavailable
from intraday.execution.perp_control import PerpController
from intraday.execution.scoped_source import ScopedEvidenceSource


def run_control_cycle(source, journal, credentials, *, clock=None, venue_factory=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    account = credentials.account_ref
    with source.connect() as c:
        symbols = [r[0] for r in c.execute("SELECT symbol FROM asset_venue_routes WHERE market='perp' AND venue='bnb' AND environment='demo' ORDER BY symbol")]
    factory = venue_factory or (lambda symbol: BinanceDemoAdapter(
        DemoTransport(credentials, settings_enabled=True), symbol=symbol, market_scoped=True))
    results = []
    for symbol in symbols:
        scoped = ScopedEvidenceSource(source.path, symbol=symbol, market="perp")
        controller = PerpController(scoped, factory(symbol), journal, clock=clock)
        try:
            for item in journal.settings_requests(account):
                if item["symbol"] == symbol:
                    results.append(controller.process(item["id"]))
            info = controller.information()
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, ExecutionUnavailable):
            # No raw exchange payload, credential or exception traceback projection.
            info = {"status":"unavailable", "symbol":symbol, "account":account.key,
                    "observed_at":clock().isoformat(), "reason":"Perp account/settings read unavailable"}
        journal.save_perp_snapshot(account, symbol, info)
    return {"status":"observed", "pairs":len(symbols), "requests":results, "orders_enabled":False}
