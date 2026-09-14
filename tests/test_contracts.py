import pytest
from pydantic import ValidationError

from lab.contracts import ExperimentSpec
from lab.experiment import read_config


def payload():
    return {
        "title": "QQQ trend check",
        "hypothesis": "A faster average may react sooner.",
        "symbol": "qqq",
        "data": {"start": "2019-01-01", "end_exclusive": "2025-01-01"},
        "strategy": {"family": "sma_crossover", "fast_window": 10, "slow_window": 30},
        "initial_cash": 5000,
        "slippage_bps": [0, 5, 10],
        "commission": 0,
        "periods": {
            "learning": ["2020-01-01", "2022-12-31"],
            "evaluation": ["2023-01-01", "2024-12-31"],
        },
    }


def test_root_config_uses_typed_contract():
    config = read_config()
    assert config.symbol == "SPY"
    assert config.engine_symbol == "SPY.US"
    assert config.strategy.label == "SMA 20/50"


def test_contract_normalizes_user_input():
    config = ExperimentSpec.model_validate(payload())
    assert config.symbol == "QQQ"
    assert config.title == "QQQ trend check"
    assert config.to_json_dict()["data"]["start"] == "2019-01-01"


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "AAPL"},
        {"strategy": {"family": "sma_crossover", "fast_window": 50, "slow_window": 20}},
        {"slippage_bps": [5, 5]},
        {"periods": {"one": ["2020-01-01", "2023-01-01"],
                     "two": ["2023-01-01", "2024-01-01"]}},
    ],
)
def test_contract_rejects_out_of_scope_or_ambiguous_input(change):
    value = payload()
    value.update(change)
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(value)
