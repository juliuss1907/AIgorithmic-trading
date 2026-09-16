import json
import pandas as pd
import pytest

from lab.contracts import CandidateLock, ExperimentSpec
from lab.data import save_snapshot
from lab.datasets import DatasetCatalog
from lab.evaluation import PromotionDecision, PromotionStore
from lab.holdout import HoldoutPipeline
from lab.store import RunStore


def frame(end="2026-08-31"):
    index = pd.date_range("2017-08-17", end, freq="D")
    close = pd.Series(range(4000, 4000 + len(index)), index=index, dtype=float)
    return pd.DataFrame({
        "open": close, "high": close + 10, "low": close - 10,
        "close": close + 5, "adj_close": close + 5, "volume": 10,
    }, index=index)


def config_dict(parent_run_id="b" * 20):
    years = [str(year) for year in range(2018, 2026)]
    return {
        "title": "BTC Donchian one-shot holdout",
        "hypothesis": "Evaluate the locked candidate exactly once.",
        "market": "crypto_spot", "venue": "binance", "symbol": "BTCUSDT",
        "interval": "1d", "calendar": "UTC_24_7", "dataset_id": None,
        "parent_run_id": parent_run_id, "prior_observed_periods": years,
        "data": {"start": "2017-08-17", "end_exclusive": "2026-09-01"},
        "strategy": {"family": "donchian_breakout", "entry_window": 20,
                     "exit_window": 10, "atr_window": 14},
        "initial_cash": 10000, "slippage_bps": [0, 5, 10],
        "taker_fee_bps": 10, "commission": 0,
        "risk_policy": {
            "max_target_weight": .5, "halt_drawdown": .2,
            "position_sizing": {"family": "entry_volatility", "lookback": 20,
                                "annual_target": .2, "annualization_days": 365},
        },
        "periods": {
            **{year: [f"{year}-01-01", f"{year}-12-31"] for year in years},
            "holdout": ["2026-01-01", "2026-08-31"],
        },
    }


def summary_metric(total_return=.03, max_drawdown=-.12, start="2026-01-01", end="2026-08-31"):
    return {
        "final_equity": 10000 * (1 + total_return), "total_return": total_return,
        "cagr_252": total_return, "cagr_annualized": total_return,
        "annualization_days": 365, "max_drawdown": max_drawdown,
        "round_trips": 2, "fills": 4, "time_in_market": .5,
        "mean_holding_sessions": 20, "sessions": 243, "start": start, "end": end,
    }


def write_complete_run(config, output, catalog=None, total_return=.03, max_drawdown=-.12):
    output.mkdir(parents=True)
    summary = {}
    for period, dates in config.periods.items():
        for bps in config.slippage_bps:
            for strategy in ("rule", "buy-hold-50", "buy-hold-100"):
                summary[f"{period}/{bps:g}bps/{strategy}"] = summary_metric(
                    total_return if period == "holdout" else .01,
                    max_drawdown if period == "holdout" else -.1,
                    str(dates[0]), str(dates[1]),
                )
    (output / "summary.json").write_text(json.dumps(summary))
    (output / "provenance.json").write_text(json.dumps({
        "experiment": config.to_json_dict(), "data": {"symbol": "BTCUSDT"},
    }))
    (output / "report.md").write_text("# holdout\n")
    (output / "equity.png").write_bytes(b"png")


@pytest.fixture
def setup_pipeline(tmp_path):
    catalog = DatasetCatalog(tmp_path / "data")
    learning_spec = ExperimentSpec.model_validate(config_dict() | {
        "dataset_id": None,
        "data": {"start": "2017-08-17", "end_exclusive": "2026-01-01"},
        "periods": {year: [f"{year}-01-01", f"{year}-12-31"]
                    for year in map(str, range(2018, 2026))},
    })
    learning = save_snapshot(
        frame("2025-12-31"), learning_spec, catalog=catalog,
        retrieved_at_utc="2026-01-01T00:00:00+00:00",
        metadata={"exchange_rules": {"quantity_step": "0.00001000", "min_notional": "5.00000000"}},
    )
    lock = CandidateLock(
        candidate="donchian_breakout", run_id="b" * 20, dataset_id=learning.id,
        parent_run_id="a" * 20,
        strategy=config_dict()["strategy"],
        position_sizing=config_dict()["risk_policy"]["position_sizing"],
        summary_sha256="c" * 64, provenance_sha256="d" * 64,
    )
    promotion = PromotionStore(tmp_path / "state/promotion.sqlite3")
    promotion.freeze(PromotionDecision("candidate_selected", "donchian_breakout", ()))
    promotion.lock_candidate(lock)
    config_path = tmp_path / "holdout.json"
    config_path.write_text(json.dumps(config_dict()))
    run_store = RunStore(tmp_path / "state/lab.sqlite3", tmp_path / "runs")
    output = tmp_path / "runs/holdout"
    return catalog, promotion, run_store, config_path, output


def pipeline(setup_pipeline, *, downloaded=None, runner=write_complete_run):
    catalog, promotion, run_store, config_path, output = setup_pipeline
    downloaded = frame() if downloaded is None else downloaded

    def fetch_snapshot(config, catalog):
        return save_snapshot(
            downloaded, config, catalog=catalog,
            retrieved_at_utc="2026-09-01T00:00:00+00:00",
            metadata={"exchange_rules": {"quantity_step": "0.00001000", "min_notional": "5.00000000"}},
        )

    return HoldoutPipeline(
        promotion, run_store, catalog, config_path, output,
        fetch_snapshot=fetch_snapshot, execute_run=runner,
    )


def test_pipeline_fetches_matching_history_runs_and_latches_verified_evidence(setup_pipeline):
    result = pipeline(setup_pipeline).execute()

    assert result["candidate"] == "donchian_breakout"
    assert result["dataset_id"] != setup_pipeline[1].get()["candidate_lock"]["dataset_id"]
    assert result["period"] == "2026-01-01/2026-08-31"
    assert result["passed"] is True
    assert len(result["run_id"]) == 20
    assert len(result["summary_sha256"]) == len(result["provenance_sha256"]) == 64
    assert setup_pipeline[1].get()["holdout"] == result


def test_pipeline_rejects_revised_learning_history(setup_pipeline):
    changed = frame()
    changed.iloc[0, changed.columns.get_loc("close")] += 1
    changed.iloc[0, changed.columns.get_loc("adj_close")] += 1

    with pytest.raises(ValueError, match="learning history"):
        pipeline(setup_pipeline, downloaded=changed).execute()
    assert setup_pipeline[1].get()["holdout"] is None


@pytest.mark.parametrize("mutation, message", [
    (lambda value: value["periods"].update({"holdout": ["2026-02-01", "2026-08-31"]}), "period"),
    (lambda value: value["strategy"].update({"entry_window": 21}), "contract"),
    (lambda value: value.update({"parent_run_id": "e" * 20}), "parent"),
])
def test_pipeline_rejects_config_drift_before_fetch(setup_pipeline, mutation, message):
    config_path = setup_pipeline[3]
    value = json.loads(config_path.read_text())
    mutation(value)
    config_path.write_text(json.dumps(value))

    with pytest.raises(ValueError, match=message):
        pipeline(setup_pipeline, runner=lambda *_: pytest.fail("must not run")).execute()


def test_pipeline_stops_on_missing_day(setup_pipeline):
    missing = frame().drop(pd.Timestamp("2026-04-01"))
    with pytest.raises(ValueError, match="2026-04-01"):
        pipeline(setup_pipeline, downloaded=missing).execute()


def test_pipeline_recovers_complete_run_created_before_latch(setup_pipeline):
    catalog, promotion, _, _, output = setup_pipeline
    snapshot = save_snapshot(
        frame(), ExperimentSpec.model_validate(config_dict()), catalog=catalog,
        retrieved_at_utc="2026-09-01T00:00:00+00:00",
        metadata={"exchange_rules": {"quantity_step": "0.00001000", "min_notional": "5.00000000"}},
    )
    recovered_config = ExperimentSpec.model_validate(config_dict() | {"dataset_id": snapshot.id})
    write_complete_run(recovered_config, output)

    result = pipeline(
        setup_pipeline,
        runner=lambda *_: pytest.fail("complete artifact must be resumed, not rerun"),
    ).execute()

    assert result["passed"] is True
    assert promotion.get()["holdout"]["run_id"] == result["run_id"]
    assert pipeline(setup_pipeline).execute() == result
