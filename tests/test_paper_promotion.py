import hashlib
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from lab.paper import PaperTradingService
from lab.paper_promotion import activate_promoted_account, verify_promoted_contract


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def promotion_fixture(tmp_path):
    artifacts = {}
    for run_id, dataset_id in [("b" * 20, "c" * 64), ("f" * 20, "a" * 64)]:
        root = tmp_path / run_id
        root.mkdir()
        summary = root / "summary.json"
        provenance = root / "provenance.json"
        summary.write_text(json.dumps({"result": "verified"}))
        provenance.write_text(json.dumps({
            "experiment": {
                "title": "Locked BTC contract", "hypothesis": "Frozen before holdout",
                "market": "crypto_spot", "venue": "binance", "symbol": "BTCUSDT",
                "interval": "1d", "calendar": "UTC_24_7", "dataset_id": dataset_id,
                "data": {"start": "2017-08-17", "end_exclusive": "2026-09-01"},
                "periods": {"holdout": ["2026-01-01", "2026-08-31"]},
                "prior_observed_periods": ["2018", "2019", "2020", "2021", "2022",
                                           "2023", "2024", "2025"],
                "strategy": {"family": "donchian_breakout", "entry_window": 20,
                             "exit_window": 10, "atr_window": 14},
                "risk_policy": {
                    "max_target_weight": .5, "halt_drawdown": .2,
                    "position_sizing": {"family": "entry_volatility", "lookback": 20,
                                        "annual_target": .2, "annualization_days": 365},
                },
                "initial_cash": 10000, "slippage_bps": [0, 5, 10],
                "taker_fee_bps": 10, "commission": 0,
            }
        }))
        artifacts[run_id] = {"summary.json": summary, "provenance.json": provenance}

    lock = {
        "candidate": "donchian_breakout", "run_id": "b" * 20,
        "dataset_id": "c" * 64, "parent_run_id": "9" * 20,
        "strategy": {"family": "donchian_breakout", "entry_window": 20,
                     "exit_window": 10, "atr_window": 14},
        "position_sizing": {"family": "entry_volatility", "lookback": 20,
                            "annual_target": .2, "annualization_days": 365},
        "summary_sha256": sha(artifacts["b" * 20]["summary.json"]),
        "provenance_sha256": sha(artifacts["b" * 20]["provenance.json"]),
    }
    holdout = {
        "candidate": "donchian_breakout", "period": "2026-01-01/2026-08-31",
        "run_id": "f" * 20, "dataset_id": "a" * 64,
        "summary_sha256": sha(artifacts["f" * 20]["summary.json"]),
        "provenance_sha256": sha(artifacts["f" * 20]["provenance.json"]),
        "total_return": .07, "max_drawdown": -.08, "passed": True,
    }

    class Promotion:
        def get(self):
            return {"status": "candidate_selected", "selected": "donchian_breakout",
                    "candidate_lock": lock, "holdout": holdout}

    class Runs:
        def get_run(self, run_id):
            dataset = lock["dataset_id"] if run_id == lock["run_id"] else holdout["dataset_id"]
            return {"id": run_id, "dataset_id": dataset}

        def list_artifacts(self, run_id):
            return [{"id": name, "relative_path": name} for name in artifacts[run_id]]

        def artifact(self, run_id, artifact_id):
            return artifacts[run_id][artifact_id], {}

    snapshot = SimpleNamespace(
        symbol="BTCUSDT", market="crypto_spot", venue="binance", interval="1d",
        calendar="UTC_24_7", exchange_rules={"quantity_step": "0.00001000",
                                               "min_notional": "5.00000000"},
    )

    class Catalog:
        def get(self, _snapshot_id):
            return snapshot

        def load(self, _snapshot_id):
            return None, {"exchange_rules": snapshot.exchange_rules}

    return Promotion(), Runs(), Catalog(), artifacts, lock


def test_verified_promotion_contract_checks_runs_datasets_and_execution(tmp_path):
    promotion, runs, catalog, _, lock = promotion_fixture(tmp_path)

    verified = verify_promoted_contract(promotion, runs, catalog)

    assert verified["candidate_run_id"] == lock["run_id"]
    assert verified["exchange_rules"]["min_notional"] == "5.00000000"
    assert len(verified["contract_sha256"]) == 64


def test_verified_promotion_contract_rejects_changed_artifact(tmp_path):
    promotion, runs, catalog, artifacts, _ = promotion_fixture(tmp_path)
    artifacts["f" * 20]["summary.json"].write_text("changed")

    with pytest.raises(ValueError, match="checksum"):
        verify_promoted_contract(promotion, runs, catalog)


def test_activation_bootstraps_once_and_reuses_the_promoted_account(tmp_path):
    promotion, runs, catalog, _, _ = promotion_fixture(tmp_path)
    paper = PaperTradingService(tmp_path / "paper.sqlite3", log_dir=tmp_path / "logs")
    index = pd.date_range("2026-01-01", periods=25)
    close = pd.Series(range(100, 125), index=index, dtype=float)

    class Gateway:
        def snapshot(self):
            return {
                "frame": pd.DataFrame({
                    "open": close, "high": close + 1, "low": close - 1,
                    "close": close, "volume": 1,
                }, index=index),
                "dataset_snapshot_id": "a" * 64,
                "exchange_rules": {"quantity_step": "0.00001000",
                                   "min_notional": "5.00000000"},
                "bid": 124., "ask": 125.,
            }

    first = activate_promoted_account(promotion, runs, catalog, paper, Gateway())
    repeated = activate_promoted_account(promotion, runs, catalog, paper, Gateway())

    assert first["account"]["id"] == repeated["account"]["id"]
    assert first["bootstrap"]["id"] == repeated["bootstrap"]["id"]
    assert len(paper.list_cycles(first["account"]["id"])) == 1
    assert paper.campaign_status(first["account"]["id"])["status"] == "running"
