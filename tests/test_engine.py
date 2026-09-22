import numpy as np
import pandas as pd
import pytest

from lab.experiment import run_case
from lab.execution import CryptoSpotEngine
from lab.strategy import SignalEngine


def case(bps=0):
    return {"codes": ["SPY.US"], "start_date": "2024-01-01", "end_date": "2024-01-12",
            "evaluation_start_date": "2024-01-04", "initial_cash": 10000.,
            "fast_window": 1, "slow_window": 2, "slippage_us": bps / 10000,
            "source": "local", "position_adjustment": "hold", "leverage": 1.0}


def frame():
    return pd.DataFrame({"open": [100., 100, 100, 120, 130, 110],
                         "close": [100., 100, 120, 130, 110, 90],
                         "high": 150., "low": 80., "volume": 1000},
                        index=pd.bdate_range("2024-01-02", periods=6))


def test_next_open_and_terminal_cash(tmp_path):
    data = frame()
    config = case()
    config["evaluation_start_date"] = "2024-01-04"
    result = run_case(data, config, tmp_path / "case")
    fills = pd.read_csv(tmp_path / "case/fills-exact.csv")
    # Jan 4 close creates buy; Jan 5 open executes. Jan 8 close creates sell;
    # Jan 9 open executes at 110, not Jan 8 close's already-seen 110.
    assert fills["timestamp"].tolist() == ["2024-01-05", "2024-01-09"]
    assert fills["execution_price"].tolist() == [120, 110]
    assert result["final_equity"] == pytest.approx(10000 - 83.33 * 10)


def test_buy_hold_costs_and_terminal_liquidation(tmp_path):
    zero = run_case(frame(), case(), tmp_path / "zero", True)
    costs = run_case(frame(), case(10), tmp_path / "costs", True)
    assert zero["final_equity"] == pytest.approx(9000)
    assert costs["final_equity"] < zero["final_equity"]
    fills = pd.read_csv(tmp_path / "costs/fills-exact.csv")
    assert fills.iloc[0]["execution_price"] == pytest.approx(100.1)
    assert fills.iloc[-1]["execution_price"] == pytest.approx(89.91)
    assert fills.iloc[-1]["reason"] == "end_of_backtest"
    assert zero["time_in_market"] == 1.0


def test_all_cash_and_determinism(tmp_path):
    data = frame()
    data["open"] = data["close"] = 100.
    first = run_case(data, case(), tmp_path / "one")
    second = run_case(data, case(), tmp_path / "two")
    assert first == second
    assert first["final_equity"] == 10000
    assert first["round_trips"] == 0
    assert first["max_drawdown"] == 0


def test_truncated_history_produces_same_signals():
    data = frame()
    full = SignalEngine(1, 2).generate({"SPY.US": data})["SPY.US"]
    for end in range(1, len(data) + 1):
        partial = SignalEngine(1, 2).generate({"SPY.US": data.iloc[:end]})["SPY.US"]
        pd.testing.assert_series_equal(partial, full.iloc[:end])


def test_crypto_engine_applies_step_min_notional_and_taker_fee():
    engine = CryptoSpotEngine({
        "initial_cash": 10_000, "quantity_step": "0.00001000",
        "min_notional": "5.00000000", "taker_fee_bps": 10,
        "slippage_us": .0005,
    })

    assert engine.round_size(0.123456789, 40_000) == pytest.approx(0.12345)
    assert engine.round_size(0.0001, 40_000) == 0
    assert engine.apply_slippage(40_000, 1) == 40_020
    assert engine.calc_commission(.1, 40_000, 1, True) == 4


def test_btc_case_uses_half_capital_next_open_and_365_day_cagr(tmp_path):
    data = frame()
    config = case(5) | {
        "codes": ["BTCUSDT"], "market": "crypto_spot",
        "strategy": {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        "target_weight": .5, "quantity_step": "0.00001000", "min_notional": "5",
        "taker_fee_bps": 10, "bars_per_year": 365,
    }

    result = run_case(data, config, tmp_path / "btc")
    fills = pd.read_csv(tmp_path / "btc/fills-exact.csv")

    assert fills.iloc[0]["notional"] <= 5_000
    assert fills.iloc[0]["fee"] > 0
    assert fills.iloc[0]["execution_price"] == pytest.approx(120.06)
    assert result["annualization_days"] == 365
    assert "cagr_annualized" in result
    assert (tmp_path / "btc/signal-evidence.csv").exists()


def test_btc_case_applies_entry_volatility_only_to_the_rule_strategy(tmp_path):
    log_returns = [-.02, -.10] * 10 + [.02, .08, .01, -.10]
    close = 100 * np.exp(np.r_[0, np.cumsum(log_returns)])
    dates = pd.date_range("2024-01-01", periods=len(close), freq="D")
    data = pd.DataFrame({
        "open": close,
        "high": close * 1.01,
        "low": close * .99,
        "close": close,
        "volume": 1000,
    }, index=dates)
    config = case(5) | {
        "codes": ["BTCUSDT"], "market": "crypto_spot",
        "start_date": str(dates[0].date()), "end_date": str(dates[-1].date()),
        "evaluation_start_date": str(dates[22].date()),
        "strategy": {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        "target_weight": .5, "quantity_step": "0.00001000", "min_notional": "5",
        "taker_fee_bps": 10, "bars_per_year": 365,
        "position_sizing": {
            "family": "entry_volatility", "lookback": 20,
            "annual_target": .2, "annualization_days": 365,
        },
    }

    run_case(data, config, tmp_path / "risk")
    evidence = pd.read_csv(tmp_path / "risk/signal-evidence.csv")

    assert evidence["position_sizing_family"].eq("entry_volatility").all()
    assert 0 < evidence.loc[21, "target"] < .5
    assert evidence.loc[21:23, "target"].nunique() == 1
