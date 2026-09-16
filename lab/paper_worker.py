"""09:00 Vietnam paper worker backed only by Binance public market-data APIs."""

import argparse
import time
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path

from lab.contracts import DatasetRequest
from lab.data import DATA, ROOT, BinanceClient, save_snapshot
from lab.datasets import DatasetCatalog
from lab.paper import PaperTradingService


class BinancePaperGateway:
    def __init__(self, catalog=None, client=None):
        self.catalog = catalog or DatasetCatalog(DATA)
        self.client = client or BinanceClient()

    def snapshot(self):
        today = datetime.now(timezone.utc).date()
        request = DatasetRequest(
            market="crypto_spot", venue="binance", symbol="BTCUSDT",
            interval="1d", calendar="UTC_24_7", start=date(2017, 8, 17),
            end_exclusive=today,
        )
        downloaded = self.client.history(request)
        frozen = save_snapshot(downloaded, request, catalog=self.catalog)
        frame, _ = self.catalog.load(frozen.id)
        quote = self.client.book_ticker()
        return {
            "frame": frame, "dataset_snapshot_id": frozen.id,
            "exchange_rules": downloaded.metadata["exchange_rules"], **quote,
        }


def seconds_until_utc_cycle(now=None):
    now = now or datetime.now(timezone.utc)
    target = datetime.combine(now.date(), clock_time(2, 0), tzinfo=timezone.utc)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


class PaperWorker:
    def __init__(self, service=None, gateway=None):
        self.service = service or PaperTradingService(ROOT / "state/lab.sqlite3")
        self.gateway = gateway or BinancePaperGateway()

    def run_once(self, account_id=None):
        accounts = (
            [self.service.get_account(account_id)]
            if account_id else [item for item in self.service.list_accounts() if item["status"] == "active"]
        )
        if not accounts:
            return []
        expected_candle = datetime.now(timezone.utc).date() - timedelta(days=1)
        try:
            market = self.gateway.snapshot()
        except Exception as exc:
            for account in accounts:
                try:
                    self.service.record_incident(
                        account["id"], expected_candle, "data_fetch_failed",
                        f"{type(exc).__name__}: {exc}",
                    )
                except KeyError:
                    pass
            raise
        latest_candle = market["frame"].index[-1].date()
        results = []
        for account in accounts:
            try:
                self.service.recover_incident(account["id"], expected_candle)
                if account["last_candle"]:
                    missing = date.fromisoformat(account["last_candle"]) + timedelta(days=1)
                    while missing < latest_candle:
                        self.service.record_incident(
                            account["id"], missing, "missed_cycle",
                            "No completed paper cycle exists for this closed UTC candle",
                        )
                        missing += timedelta(days=1)
            except KeyError:
                pass
            results.append(self.service.run_cycle(
                account["id"], market["frame"], bid=market["bid"], ask=market["ask"],
                exchange_rules=market["exchange_rules"],
                dataset_snapshot_id=market["dataset_snapshot_id"], entry_point="scheduler",
            ))
        return results

    def run_forever(self):
        while True:
            remaining = seconds_until_utc_cycle()
            while remaining > 0:
                interval = min(remaining, 60)
                time.sleep(interval)
                remaining -= interval
            self.run_once()


def main():
    parser = argparse.ArgumentParser(
        description="BTCUSDT paper worker at 09:00 Asia/Ho_Chi_Minh (02:00 UTC)"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--account")
    parser.add_argument("--database", type=Path, default=ROOT / "state/lab.sqlite3")
    parser.add_argument("--data-dir", type=Path, default=DATA)
    args = parser.parse_args()
    worker = PaperWorker(
        PaperTradingService(args.database), BinancePaperGateway(DatasetCatalog(args.data_dir))
    )
    if args.once:
        for cycle in worker.run_once(args.account):
            print(cycle["id"])
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()
