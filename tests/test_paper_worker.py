import pytest
import sys
from datetime import datetime, timezone

import pandas as pd

from lab.paper import PaperTradingService
from lab.paper_worker import PaperWorker, main, seconds_until_utc_cycle
from lab.notifications import TelegramDeliveryError


def promoted_account(service):
    class Gate:
        def get(self):
            return {
                "status": "candidate_selected", "selected": "donchian_breakout",
                "candidate_lock": {
                    "candidate": "donchian_breakout", "run_id": "b" * 20,
                    "dataset_id": "c" * 64,
                    "strategy": {"family": "donchian_breakout", "entry_window": 20,
                                 "exit_window": 10, "atr_window": 14},
                    "position_sizing": {"family": "entry_volatility", "lookback": 20,
                                        "annual_target": .2, "annualization_days": 365},
                },
                "holdout": {"candidate": "donchian_breakout", "passed": True,
                            "run_id": "f" * 20, "dataset_id": "a" * 64},
            }

    return service.create_promoted_account(Gate())


def market_frame():
    index = pd.date_range("2026-09-14", periods=2)
    return pd.DataFrame({
        "open": [100., 101.], "high": [101., 102.], "low": [99., 100.],
        "close": [100., 101.], "volume": [1., 1.],
    }, index=index)


class MarketGateway:
    def snapshot(self):
        return {
            "frame": market_frame(), "dataset_snapshot_id": "d" * 64,
            "exchange_rules": {"quantity_step": "0.00001000", "min_notional": "5.00000000"},
            "bid": 100., "ask": 101.,
        }


def test_worker_runs_public_snapshot_to_idempotent_cycle(tmp_path):
    service = PaperTradingService(tmp_path / "paper.sqlite3")
    account = service.create_account(
        {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        dataset_snapshot_id="a" * 64,
    )
    index = pd.date_range("2025-01-01", periods=2)
    frame = pd.DataFrame({
        "open": [100., 120], "high": [101., 121], "low": [99., 119],
        "close": [100., 120], "volume": [1., 1.],
    }, index=index)

    class Gateway:
        def snapshot(self):
            return {
                "frame": frame, "dataset_snapshot_id": "b" * 64,
                "exchange_rules": {"quantity_step": "0.00001000", "min_notional": "5.00000000"},
                "bid": 100., "ask": 101.,
            }

    worker = PaperWorker(service, Gateway())
    first = worker.run_once(account["id"])
    second = worker.run_once(account["id"])

    assert first[0]["id"] == second[0]["id"]
    assert first[0]["dataset_snapshot_id"] == "b" * 64
    assert len(service.list_fills(account["id"])) == 1


def test_scheduler_targets_nine_in_the_morning_vietnam():
    now = datetime(2026, 9, 15, 1, 59, 30, tzinfo=timezone.utc)
    assert seconds_until_utc_cycle(now) == 30
    after = datetime(2026, 9, 15, 2, 1, tzinfo=timezone.utc)
    assert seconds_until_utc_cycle(after) == 23 * 3600 + 59 * 60


def test_worker_audits_fetch_failure_and_recovers_on_same_day(tmp_path):
    service = PaperTradingService(tmp_path / "paper.sqlite3", log_dir=tmp_path / "logs")

    class Gate:
        def get(self):
            return {
                "status": "candidate_selected", "selected": "donchian_breakout",
                "candidate_lock": {
                    "candidate": "donchian_breakout", "run_id": "b" * 20,
                    "dataset_id": "c" * 64,
                    "strategy": {"family": "donchian_breakout", "entry_window": 20,
                                 "exit_window": 10, "atr_window": 14},
                    "position_sizing": {"family": "entry_volatility", "lookback": 20,
                                        "annual_target": .2, "annualization_days": 365},
                },
                "holdout": {"candidate": "donchian_breakout", "passed": True,
                            "run_id": "f" * 20, "dataset_id": "a" * 64},
            }

    account = service.create_promoted_account(Gate())

    class FailedGateway:
        def snapshot(self):
            raise OSError("network unavailable")

    with pytest.raises(OSError, match="network unavailable"):
        PaperWorker(service, FailedGateway()).run_once(account["id"])
    incident = service.list_incidents(account["id"])[0]
    assert incident["kind"] == "data_fetch_failed"
    assert incident["status"] == "open"

    index = pd.date_range("2026-09-01", periods=2)
    frame = pd.DataFrame({
        "open": [100., 101.], "high": [101., 102.], "low": [99., 100.],
        "close": [100., 101.], "volume": [1., 1.],
    }, index=index)

    class RecoveredGateway:
        def snapshot(self):
            return {
                "frame": frame, "dataset_snapshot_id": "d" * 64,
                "exchange_rules": {"quantity_step": "0.00001000", "min_notional": "5.00000000"},
                "bid": 100., "ask": 101.,
            }

    PaperWorker(service, RecoveredGateway()).run_once(account["id"])

    assert service.list_incidents(account["id"])[0]["status"] == "recovered"


def test_worker_sends_one_daily_summary_for_an_idempotent_cycle(tmp_path):
    service = PaperTradingService(tmp_path / "paper.sqlite3", log_dir=tmp_path / "logs")
    account = promoted_account(service)

    class Recorder:
        def __init__(self):
            self.messages = []

        def send(self, message):
            self.messages.append(message)
            return {"message_id": len(self.messages)}

    notifier = Recorder()
    worker = PaperWorker(service, MarketGateway(), notifier)

    first = worker.run_once(account["id"])
    repeated = worker.run_once(account["id"])

    assert first[0]["id"] == repeated[0]["id"]
    assert len(notifier.messages) == 1
    assert "BTC PAPER · 2026-09-15" in notifier.messages[0]
    assert "Campaign: 1/56" in notifier.messages[0]
    assert "Đối soát: OK" in notifier.messages[0]
    assert service.list_pending_notifications() == []


def test_worker_sends_fetch_failure_once_across_systemd_retries(tmp_path):
    service = PaperTradingService(tmp_path / "paper.sqlite3", log_dir=tmp_path / "logs")
    account = promoted_account(service)

    class FailedGateway:
        def snapshot(self):
            raise OSError("network unavailable")

    class Recorder:
        def __init__(self):
            self.messages = []

        def send(self, message):
            self.messages.append(message)
            return {"message_id": len(self.messages)}

    notifier = Recorder()
    worker = PaperWorker(service, FailedGateway(), notifier)

    for _attempt in range(2):
        with pytest.raises(OSError, match="network unavailable"):
            worker.run_once(account["id"])

    assert len(notifier.messages) == 1
    assert "KHÔNG TẢI ĐƯỢC DỮ LIỆU" in notifier.messages[0]
    assert len(service.list_incidents(account["id"])) == 1


def test_telegram_failure_does_not_fail_or_duplicate_the_paper_cycle(tmp_path):
    service = PaperTradingService(tmp_path / "paper.sqlite3", log_dir=tmp_path / "logs")
    account = promoted_account(service)

    class UnavailableTelegram:
        def send(self, _message):
            raise TelegramDeliveryError("Telegram request failed")

    worker = PaperWorker(service, MarketGateway(), UnavailableTelegram())

    cycles = worker.run_once(account["id"])

    assert cycles[0]["status"] == "completed"
    assert cycles[0]["reconciliation_ok"] is True
    assert len(service.list_cycles(account["id"])) == 1
    pending = service.list_pending_notifications()
    assert len(pending) == 1
    assert pending[0]["status"] == "failed"
    assert pending[0]["last_error"] == "telegram_delivery_error"


def test_worker_main_loads_optional_telegram_notifier(monkeypatch, tmp_path):
    configured = object()
    captured = {}

    class Worker:
        def __init__(self, service, gateway, notifier):
            captured["arguments"] = (service, gateway, notifier)

        def run_once(self, _account):
            return []

    monkeypatch.setattr("lab.paper_worker.telegram_from_environment", lambda: configured)
    monkeypatch.setattr("lab.paper_worker.PaperTradingService", lambda path: ("paper", path))
    monkeypatch.setattr("lab.paper_worker.DatasetCatalog", lambda path: ("catalog", path))
    monkeypatch.setattr("lab.paper_worker.BinancePaperGateway", lambda catalog: ("gateway", catalog))
    monkeypatch.setattr("lab.paper_worker.PaperWorker", Worker)
    monkeypatch.setattr(sys, "argv", [
        "paper-worker", "--once", "--database", str(tmp_path / "lab.sqlite3"),
        "--data-dir", str(tmp_path / "data"),
    ])

    main()

    assert captured["arguments"][2] is configured
