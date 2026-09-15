from datetime import datetime, timezone

import pandas as pd

from lab.paper import PaperTradingService
from lab.paper_worker import PaperWorker, seconds_until_utc_cycle


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


def test_scheduler_targets_two_minutes_after_utc_midnight():
    now = datetime(2026, 9, 15, 0, 1, 30, tzinfo=timezone.utc)
    assert seconds_until_utc_cycle(now) == 30
    after = datetime(2026, 9, 15, 0, 3, tzinfo=timezone.utc)
    assert seconds_until_utc_cycle(after) == 23 * 3600 + 59 * 60
