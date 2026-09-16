"""One-shot holdout execution bound to a locked promotion candidate."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

from lab.contracts import ExperimentSpec, HoldoutResult
from lab.data import fetch
from lab.evaluation import LEARNING_FOLDS
from lab.experiment import read_config, run
from lab.report import generate


HOLDOUT_PERIOD = ("2026-01-01", "2026-08-31")


class HoldoutPipeline:
    def __init__(
        self, promotion_store, run_store, catalog, config_path, output,
        *, fetch_snapshot=fetch, execute_run=run, generate_report=generate,
    ):
        self.promotion_store = promotion_store
        self.run_store = run_store
        self.catalog = catalog
        self.config_path = Path(config_path)
        self.output = Path(output).resolve()
        self.fetch_snapshot = fetch_snapshot
        self.execute_run = execute_run
        self.generate_report = generate_report

    @staticmethod
    def _validate_config(config, lock):
        if config.dataset_id is not None:
            raise ValueError("Holdout config must leave dataset_id unset until fetch")
        if tuple(map(str, config.periods.get("holdout", ()))) != HOLDOUT_PERIOD:
            raise ValueError("Holdout period must be 2026-01-01 through 2026-08-31")
        if config.parent_run_id != lock["run_id"]:
            raise ValueError("Holdout parent must be the locked learning run")
        expected_periods = (*LEARNING_FOLDS, "holdout")
        if tuple(config.periods) != expected_periods or tuple(config.prior_observed_periods) != LEARNING_FOLDS:
            raise ValueError("Holdout period registry differs from the preregistered contract")
        for year in LEARNING_FOLDS:
            if tuple(map(str, config.periods[year])) != (f"{year}-01-01", f"{year}-12-31"):
                raise ValueError("Observed learning period differs from the preregistered contract")
        actual = {
            "market": config.market, "venue": config.venue, "symbol": config.symbol,
            "interval": config.interval, "calendar": config.calendar,
            "data_start": str(config.data.start),
            "learning_end_exclusive": "2026-01-01",
            "initial_cash": config.initial_cash,
            "slippage_bps": list(config.slippage_bps),
            "taker_fee_bps": config.taker_fee_bps, "commission": config.commission,
            "max_target_weight": config.risk_policy.max_target_weight,
            "halt_drawdown": config.risk_policy.halt_drawdown,
            "strategy": config.strategy.model_dump(mode="json"),
            "position_sizing": config.risk_policy.position_sizing.model_dump(mode="json"),
        }
        expected = {key: lock[key] for key in actual}
        expected["slippage_bps"] = list(expected["slippage_bps"])
        if actual != expected or str(config.data.end_exclusive) != "2026-09-01":
            raise ValueError("Holdout config contract differs from the candidate lock")

    def _validate_history(self, snapshot, lock):
        learning = self.catalog.artifact_bytes(lock["dataset_id"])
        extended = self.catalog.artifact_bytes(snapshot.id)
        for name, expected in learning.items():
            expected_lines = expected.splitlines(keepends=True)
            actual_lines = extended[name].splitlines(keepends=True)
            if actual_lines[:len(expected_lines)] != expected_lines:
                raise ValueError("Downloaded snapshot revised the locked learning history")

    @staticmethod
    def _validate_snapshot(snapshot, config):
        expected = (
            config.market, config.venue, config.symbol, config.interval, config.calendar,
            str(config.data.start), str(config.data.end_exclusive),
        )
        actual = (
            snapshot.market, snapshot.venue, snapshot.symbol, snapshot.interval, snapshot.calendar,
            str(snapshot.start), str(snapshot.end + timedelta(days=1)),
        )
        if actual != expected:
            raise ValueError("Fetched dataset snapshot contract differs from the holdout config")

    @staticmethod
    def _with_dataset(config, dataset_id):
        payload = config.to_json_dict()
        payload["dataset_id"] = dataset_id
        return ExperimentSpec.model_validate(payload)

    def _artifact(self, run_id, relative_path):
        item = next(
            (value for value in self.run_store.list_artifacts(run_id)
             if value["relative_path"] == relative_path), None,
        )
        if item is None:
            raise ValueError(f"Holdout run is missing {relative_path}")
        return self.run_store.artifact(run_id, item["id"])[0]

    def _verify_run(self, run_record, expected_config):
        if run_record["dataset_id"] != expected_config.dataset_id:
            raise ValueError("Holdout run dataset differs from fetched snapshot")
        if run_record["parent_run_id"] != expected_config.parent_run_id:
            raise ValueError("Holdout run parent differs from the candidate lock")
        summary_path = self._artifact(run_record["id"], "summary.json")
        provenance_path = self._artifact(run_record["id"], "provenance.json")
        provenance = json.loads(provenance_path.read_text())
        actual_config = ExperimentSpec.model_validate(provenance["experiment"])
        if actual_config.to_json_dict() != expected_config.to_json_dict():
            raise ValueError("Holdout run config differs from the preregistered contract")
        summary = json.loads(summary_path.read_text())
        try:
            metrics = summary["holdout/5bps/rule"]
        except KeyError as exc:
            raise ValueError("Holdout run is missing base-cost rule metrics") from exc
        return HoldoutResult(
            candidate="donchian_breakout",
            period="2026-01-01/2026-08-31",
            run_id=run_record["id"], dataset_id=expected_config.dataset_id,
            summary_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(),
            provenance_sha256=hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
            total_return=float(metrics["total_return"]),
            max_drawdown=float(metrics["max_drawdown"]),
            passed=float(metrics["total_return"]) > 0 and float(metrics["max_drawdown"]) >= -.20,
        )

    def execute(self):
        self.run_store._inside_runs(self.output)
        state = self.promotion_store.get()
        lock = state.get("candidate_lock")
        if lock is None:
            raise ValueError("Holdout requires a locked candidate")
        config = read_config(self.config_path)
        self._validate_config(config, lock)
        if self.output.exists():
            run_record = self.run_store.import_run(self.output)
            provenance_path = self._artifact(run_record["id"], "provenance.json")
            recovered = ExperimentSpec.model_validate(
                json.loads(provenance_path.read_text())["experiment"]
            )
            snapshot = self.catalog.get(recovered.dataset_id)
            expected_config = self._with_dataset(config, snapshot.id)
        else:
            if state.get("holdout") is not None:
                raise ValueError("Recorded holdout artifact is missing")
            snapshot = self.fetch_snapshot(config, self.catalog)
            expected_config = self._with_dataset(config, snapshot.id)
            self._validate_snapshot(snapshot, config)
            self._validate_history(snapshot, lock)
            self.execute_run(expected_config, self.output, catalog=self.catalog)
            if not (self.output / "report.md").is_file() or not (self.output / "equity.png").is_file():
                self.generate_report(self.output)
            run_record = self.run_store.import_run(self.output)
        self._validate_snapshot(snapshot, config)
        self._validate_history(snapshot, lock)
        result = self._verify_run(run_record, expected_config)
        return self.promotion_store.open_holdout(result)
