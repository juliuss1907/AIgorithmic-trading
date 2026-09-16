import pytest
from pydantic import ValidationError

from lab.contracts import DatasetRequest, ExperimentSpec
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
    assert config.parent_run_id is None
    assert config.prior_observed_periods == ()


def test_contract_preserves_clone_lineage_and_observed_periods():
    value = payload()
    value["parent_run_id"] = "a" * 20
    value["prior_observed_periods"] = ["learning", "evaluation"]

    config = ExperimentSpec.model_validate(value)

    assert config.parent_run_id == "a" * 20
    assert config.prior_observed_periods == ("learning", "evaluation")


def test_contract_rejects_observed_period_not_present_in_experiment():
    value = payload()
    value["prior_observed_periods"] = ["future_holdout"]

    with pytest.raises(ValidationError, match="prior_observed_periods"):
        ExperimentSpec.model_validate(value)


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


def btc_payload():
    return {
        "title": "BTC daily trend",
        "hypothesis": "Daily trend may survive spot costs.",
        "market": "crypto_spot",
        "venue": "binance",
        "symbol": "btcusdt",
        "interval": "1d",
        "calendar": "UTC_24_7",
        "data": {"start": "2017-08-17", "end_exclusive": "2026-09-15"},
        "strategy": {"family": "sma_crossover", "fast_window": 20, "slow_window": 50},
        "initial_cash": 10000,
        "slippage_bps": [0, 5, 10],
        "taker_fee_bps": 10,
        "commission": 0,
        "risk_policy": {"max_target_weight": 0.5, "halt_drawdown": 0.2},
        "periods": {"evaluation": ["2025-01-01", "2025-12-31"]},
    }


def test_crypto_contract_freezes_market_cost_and_risk_semantics():
    config = ExperimentSpec.model_validate(btc_payload())

    assert config.symbol == "BTCUSDT"
    assert config.engine_symbol == "BTCUSDT"
    assert config.bars_per_year == 365
    assert config.taker_fee_bps == 10
    assert config.risk_policy.max_target_weight == 0.5
    assert config.risk_policy.position_sizing.family == "fixed"


def test_crypto_contract_accepts_the_frozen_entry_volatility_profile():
    value = btc_payload()
    value["risk_policy"]["position_sizing"] = {
        "family": "entry_volatility",
        "lookback": 20,
        "annual_target": 0.20,
        "annualization_days": 365,
    }

    config = ExperimentSpec.model_validate(value)

    assert config.risk_policy.position_sizing.family == "entry_volatility"
    assert config.risk_policy.position_sizing.lookback == 20
    assert config.risk_policy.position_sizing.annual_target == 0.20
    assert config.risk_policy.position_sizing.annualization_days == 365


@pytest.mark.parametrize(
    "position_sizing",
    [
        {"family": "entry_volatility", "lookback": 10,
         "annual_target": 0.20, "annualization_days": 365},
        {"family": "entry_volatility", "lookback": 20,
         "annual_target": 0.25, "annualization_days": 365},
        {"family": "entry_volatility", "lookback": 20,
         "annual_target": 0.20, "annualization_days": 252},
        {"family": "unknown"},
    ],
)
def test_crypto_contract_rejects_unregistered_position_sizing_profiles(position_sizing):
    value = btc_payload()
    value["risk_policy"]["position_sizing"] = position_sizing

    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(value)


def test_dataset_request_allows_only_the_supported_binance_daily_pair():
    request = DatasetRequest.model_validate({
        "market": "crypto_spot", "venue": "binance", "symbol": "btcusdt",
        "interval": "1d", "calendar": "UTC_24_7",
        "start": "2017-08-17", "end_exclusive": "2017-08-20",
    })
    assert request.symbol == "BTCUSDT"

    bad = request.model_dump(mode="json") | {"symbol": "ETHUSDT"}
    with pytest.raises(ValidationError):
        DatasetRequest.model_validate(bad)


@pytest.mark.parametrize(
    "change",
    [
        {"venue": "yahoo"},
        {"calendar": "XNYS"},
        {"interval": "4h"},
        {"taker_fee_bps": 0},
        {"risk_policy": {"max_target_weight": 1, "halt_drawdown": 0.2}},
    ],
)
def test_crypto_contract_rejects_ambiguous_or_unsafe_mvp_combinations(change):
    value = btc_payload()
    value.update(change)

    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(value)


@pytest.mark.parametrize(
    "strategy",
    [
        {
            "family": "rsi_bollinger", "rsi_window": 14,
            "bollinger_window": 20, "bollinger_stddev": 2,
            "entry_rsi": 30, "exit_rsi": 50,
        },
        {
            "family": "donchian_breakout", "entry_window": 20,
            "exit_window": 10, "atr_window": 14,
        },
    ],
)
def test_crypto_contract_accepts_the_three_allowlisted_strategy_families(strategy):
    value = btc_payload()
    value["strategy"] = strategy

    assert ExperimentSpec.model_validate(value).strategy.family == strategy["family"]
