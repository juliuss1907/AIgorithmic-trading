import json

import pytest

from lab.evaluation import PromotionStore, score_candidate, select_candidate


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
