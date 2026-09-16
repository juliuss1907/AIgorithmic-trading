"""09:00 Vietnam paper worker backed only by Binance public market-data APIs."""

import argparse
import time
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path

from lab.contracts import DatasetRequest
from lab.data import DATA, ROOT, BinanceClient, save_snapshot
from lab.datasets import DatasetCatalog
from lab.notifications import TelegramDeliveryError
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
    def __init__(self, service=None, gateway=None, notifier=None):
        self.service = service or PaperTradingService(ROOT / "state/lab.sqlite3")
        self.gateway = gateway or BinancePaperGateway()
        self.notifier = notifier

    def _deliver_pending(self):
        if self.notifier is None:
            return
        for notification in self.service.list_pending_notifications():
            try:
                self.notifier.send(notification["message"])
            except TelegramDeliveryError:
                self.service.mark_notification_failed(
                    notification["id"], "telegram_delivery_error"
                )
            else:
                self.service.mark_notification_sent(notification["id"])

    def _queue_fetch_failure(self, accounts, expected_candle, error):
        details = f"{type(error).__name__}: {error}"[:500]
        for account in accounts:
            try:
                incident = self.service.record_incident(
                    account["id"], expected_candle, "data_fetch_failed",
                    details,
                )
                if self.notifier is not None:
                    self.service.enqueue_notification(
                        account["id"], f"incident:{incident['id']}:open", "critical",
                        "\n".join([
                            f"🚨 BTC PAPER · KHÔNG TẢI ĐƯỢC DỮ LIỆU",
                            f"Ngày nến: {expected_candle}",
                            f"Tài khoản: {account['id'][:8]}",
                            "Worker sẽ thử lại theo lịch systemd sau 10 phút.",
                        ]),
                    )
            except KeyError:
                pass

    def _summary(self, account_id, cycle):
        account = self.service.get_account(account_id)
        fill = cycle["fill"]
        fill_text = "Không"
        if fill:
            fill_text = (
                f"{fill['side'].upper()} {fill['quantity']:.8f} BTC @ {fill['price']:,.2f} USDT"
            )
        try:
            campaign = self.service.campaign_status(account_id)
            campaign_text = (
                f"{campaign['successful_cycles']}/{campaign['target_cycles']} "
                f"· còn {campaign['remaining_cycles']}"
            )
            open_incidents = sum(
                incident["status"] == "open" for incident in campaign["incidents"]
            )
        except KeyError:
            campaign_text = "Không áp dụng"
            open_incidents = 0
        halted = account["status"] == "halted"
        heading = "🚨 BTC PAPER ĐÃ DỪNG" if halted else "✅ BTC PAPER"
        lines = [
            f"{heading} · {cycle['candle_date']}",
            f"Tài khoản: {account_id[:8]}",
            f"Signal: {cycle['signal']:.2%}",
            f"Fill: {fill_text}",
            f"Equity: {account['equity']:,.2f} USDT",
            f"Cash: {account['cash']:,.2f} USDT · BTC: {account['btc_quantity']:.8f}",
            f"Drawdown: {account['drawdown']:.2%}",
            f"Đối soát: {'OK' if cycle['reconciliation_ok'] else 'LỖI'}",
            f"Campaign: {campaign_text}",
            f"Incident mở: {open_incidents}",
        ]
        if halted:
            lines.append(f"Lý do dừng: {account['halt_reason'] or 'không xác định'}")
        return "\n".join(lines)

    def run_once(self, account_id=None):
        accounts = (
            [self.service.get_account(account_id)]
            if account_id else [item for item in self.service.list_accounts() if item["status"] == "active"]
        )
        if not accounts:
            return []
        self._deliver_pending()
        expected_candle = datetime.now(timezone.utc).date() - timedelta(days=1)
        try:
            market = self.gateway.snapshot()
        except Exception as exc:
            self._queue_fetch_failure(accounts, expected_candle, exc)
            self._deliver_pending()
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
            try:
                cycle = self.service.run_cycle(
                    account["id"], market["frame"], bid=market["bid"], ask=market["ask"],
                    exchange_rules=market["exchange_rules"],
                    dataset_snapshot_id=market["dataset_snapshot_id"], entry_point="scheduler",
                )
            except Exception:
                refreshed = self.service.get_account(account["id"])
                if self.notifier is not None and refreshed["status"] == "halted":
                    self.service.enqueue_notification(
                        account["id"], f"halt:{expected_candle}:{refreshed['halt_reason']}",
                        "critical", "\n".join([
                            "🚨 BTC PAPER ĐÃ DỪNG",
                            f"Ngày nến: {expected_candle}",
                            f"Tài khoản: {account['id'][:8]}",
                            f"Lý do: {refreshed['halt_reason'] or 'không xác định'}",
                        ]),
                    )
                    self._deliver_pending()
                raise
            results.append(cycle)
            if self.notifier is not None:
                refreshed = self.service.get_account(account["id"])
                self.service.enqueue_notification(
                    account["id"], f"cycle:{cycle['id']}:summary",
                    "critical" if refreshed["status"] == "halted" else "daily_summary",
                    self._summary(account["id"], cycle),
                )
        self._deliver_pending()
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
