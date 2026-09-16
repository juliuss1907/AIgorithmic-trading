import json
import sqlite3

import pytest

from lab.evaluation import PromotionStore, lock_selected_candidate, score_candidate, select_candidate
from lab.store import ArtifactChanged, RunStore


YEARS = tuple(str(year) for year in range(2018, 2026))


def summaries(returns, *, drawdown=-.15, stress_returns=None, trades=10):
    stress_returns = returns if stress_returns is None else stress_returns
    result = {}
    for year, base, stress in zip(YEARS, returns, stress_returns):
        common = {"max_drawdown": drawdown, "round_trips": trades}
        result[f"{year}/5bps/rule"] = common | {"total_return": base}
        result[f"{year}/10bps/rule"] = common | {"total_return": stress}
    return result


def test_gate_requires_five_profitable_years_stress_profit_and_drawdown_limit():
    passing = score_candidate("trend", summaries([.1, .1, .1, .1, .1, -.02, -.02, -.02]))
    too_few = score_candidate("mean", summaries([.1, .1, .1, .1, -.01, -.01, -.01, -.01]))
    stressed = score_candidate(
        "breakout", summaries([.1] * 8, stress_returns=[-.2] * 8)
    )
    drawdown = score_candidate("risky", summaries([.1] * 8, drawdown=-.21))

    assert passing.passed is True
    assert passing.profitable_folds == 5
    assert too_few.failure_reasons == ("profitable_folds",)
    assert stressed.failure_reasons == ("stress_return",)
    assert drawdown.failure_reasons == ("drawdown",)


def test_selection_prefers_drawdown_then_median_return_then_turnover():
    low_drawdown = summaries([.05] * 8, drawdown=-.10, trades=20)
    high_return = summaries([.20] * 8, drawdown=-.15, trades=2)

    decision = select_candidate({"steady": low_drawdown, "fast": high_return})

    assert decision.selected == "steady"
    assert decision.status == "candidate_selected"


def test_no_candidate_means_cash_not_a_forced_winner():
    decision = select_candidate({"bad": summaries([-.1] * 8)})
    assert decision.selected is None
    assert decision.status == "stay_cash"


def test_promotion_store_freezes_gate_and_opens_selected_holdout_once(tmp_path):
    decision = select_candidate({"steady": summaries([.05] * 8, drawdown=-.1)})
    store = PromotionStore(tmp_path / "promotion.sqlite3")

    frozen = store.freeze(decision)
    assert frozen["selected"] == "steady"
    with pytest.raises(ValueError, match="already frozen"):
        store.freeze(select_candidate({"other": summaries([.1] * 8)}))
    with pytest.raises(ValueError, match="selected candidate"):
        store.open_holdout("other", {"total_return": .1, "max_drawdown": -.1})

    opened = store.open_holdout("steady", {"total_return": .02, "max_drawdown": -.12})
    assert opened["passed"] is True
    with pytest.raises(ValueError, match="already opened"):
        store.open_holdout("steady", {"total_return": .03, "max_drawdown": -.1})

    restarted = PromotionStore(tmp_path / "promotion.sqlite3")
    assert restarted.get()["holdout"]["total_return"] == .02


def candidate_run(tmp_path, *, returns=None):
    returns = returns or [.05] * 8
    root = tmp_path / "runs/candidate"
    root.mkdir(parents=True)
    summary = summaries(returns, drawdown=-.1)
    for year, metric in summary.items():
        period = year.split("/", 1)[0]
        metric.update({
            "final_equity": 10000 * (1 + metric["total_return"]),
            "cagr_252": metric["total_return"], "fills": 20,
            "time_in_market": .5, "mean_holding_sessions": 10,
            "sessions": 365, "start": f"{period}-01-01", "end": f"{period}-12-31",
        })
    experiment = {
        "title": "Locked Donchian candidate",
        "hypothesis": "Registered before holdout.",
        "market": "crypto_spot", "venue": "binance", "symbol": "BTCUSDT",
        "interval": "1d", "calendar": "UTC_24_7", "dataset_id": "c" * 64,
        "parent_run_id": "a" * 20,
        "prior_observed_periods": list(YEARS),
        "data": {"start": "2017-08-17", "end_exclusive": "2026-01-01"},
        "strategy": {
            "family": "donchian_breakout", "entry_window": 20,
            "exit_window": 10, "atr_window": 14,
        },
        "initial_cash": 10000, "slippage_bps": [0, 5, 10],
        "taker_fee_bps": 10, "commission": 0,
        "risk_policy": {
            "max_target_weight": .5, "halt_drawdown": .2,
            "position_sizing": {
                "family": "entry_volatility", "lookback": 20,
                "annual_target": .2, "annualization_days": 365,
            },
        },
        "periods": {year: [f"{year}-01-01", f"{year}-12-31"] for year in YEARS},
    }
    (root / "summary.json").write_text(json.dumps(summary))
    (root / "provenance.json").write_text(json.dumps({"experiment": experiment, "data": {}}))
    (root / "report.md").write_text("# candidate\n")
    (root / "equity.png").write_bytes(b"png")
    store = RunStore(tmp_path / "state/lab.sqlite3", tmp_path / "runs")
    return store, store.import_run(root), root, summary


def test_candidate_lock_verifies_registered_run_and_is_idempotent(tmp_path):
    run_store, run, _, summary = candidate_run(tmp_path)
    promotion = PromotionStore(tmp_path / "state/promotion.sqlite3")
    promotion.freeze(select_candidate({"donchian_breakout": summary}))

    first = lock_selected_candidate(promotion, run_store, run["id"])
    second = lock_selected_candidate(promotion, run_store, run["id"])

    assert first == second
    assert first["candidate"] == "donchian_breakout"
    assert first["run_id"] == run["id"]
    assert first["dataset_id"] == "c" * 64
    assert first["strategy"]["entry_window"] == 20
    assert first["position_sizing"]["family"] == "entry_volatility"
    assert len(first["summary_sha256"]) == len(first["provenance_sha256"]) == 64
    assert promotion.get()["candidate_locked_at"] is not None


def test_candidate_lock_rejects_score_drift_and_changed_artifact(tmp_path):
    run_store, run, root, summary = candidate_run(tmp_path)
    promotion = PromotionStore(tmp_path / "state/promotion.sqlite3")
    drifted = summaries([.08] * 8, drawdown=-.1)
    promotion.freeze(select_candidate({"donchian_breakout": drifted}))

    with pytest.raises(ValueError, match="score"):
        lock_selected_candidate(promotion, run_store, run["id"])

    root.joinpath("summary.json").write_text(json.dumps(summary | {"changed": {}}))
    with pytest.raises(ArtifactChanged):
        lock_selected_candidate(promotion, run_store, run["id"])


def test_candidate_lock_rejects_conflicting_second_payload(tmp_path):
    run_store, run, _, summary = candidate_run(tmp_path)
    promotion = PromotionStore(tmp_path / "state/promotion.sqlite3")
    promotion.freeze(select_candidate({"donchian_breakout": summary}))
    locked = lock_selected_candidate(promotion, run_store, run["id"])

    with pytest.raises(ValueError, match="different payload"):
        promotion.lock_candidate(locked | {"dataset_id": "d" * 64})


def test_promotion_store_migrates_legacy_gate_without_losing_decision(tmp_path):
    database = tmp_path / "promotion.sqlite3"
    decision = select_candidate({"donchian_breakout": summaries([.05] * 8)})
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE promotion_gate (singleton INTEGER PRIMARY KEY, decision_json TEXT NOT NULL, "
            "frozen_at TEXT NOT NULL, holdout_json TEXT, holdout_opened_at TEXT)"
        )
        connection.execute(
            "INSERT INTO promotion_gate VALUES (1,?,?,NULL,NULL)",
            (json.dumps(PromotionStore._decision_payload(decision)), "2026-09-16T00:00:00+00:00"),
        )

    migrated = PromotionStore(database).get()

    assert migrated["selected"] == "donchian_breakout"
    assert migrated["candidate_lock"] is None
    assert migrated["candidate_locked_at"] is None
