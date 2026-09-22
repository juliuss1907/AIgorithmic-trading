import numpy as np
import pandas as pd
import pytest

from lab.contracts import (
    DonchianStrategySpec,
    EntryVolatilityPositionSizingSpec,
    RsiBollingerStrategySpec,
    SmaStrategySpec,
)
from lab.strategy import RuleSignalEngine, SignalEngine


def test_warmup_equality_and_known_trend():
    frame = pd.DataFrame({"close": [2, 2, 2, 4, 1, 1]})
    assert SignalEngine(2, 3).generate({"SPY": frame})["SPY"].tolist() == [0, 0, 0, 1, 1, 0]


def test_future_prices_cannot_change_past_signals():
    frame = pd.DataFrame({"close": list(range(1, 81))}, dtype=float)
    before = SignalEngine().generate({"SPY": frame})["SPY"]
    frame.loc[60:, "close"] = 0.1
    after = SignalEngine().generate({"SPY": frame})["SPY"]
    pd.testing.assert_series_equal(before.iloc[:60], after.iloc[:60])


def test_bad_windows():
    with pytest.raises(ValueError):
        SignalEngine(50, 20)


def test_sma_target_is_capped_for_btc():
    frame = pd.DataFrame({"close": [2, 2, 2, 4, 1, 1]})
    engine = RuleSignalEngine(SmaStrategySpec(fast_window=2, slow_window=3), 0.5)
    assert engine.generate({"BTCUSDT": frame})["BTCUSDT"].tolist() == [0, 0, 0, .5, .5, 0]


def test_entry_volatility_sizes_once_and_holds_weight_until_exit():
    log_returns = [-.02, -.10] * 10 + [.02, .08, .01, -.10]
    close = 100 * np.exp(np.r_[0, np.cumsum(log_returns)])
    frame = pd.DataFrame({"close": close})
    engine = RuleSignalEngine(
        SmaStrategySpec(fast_window=1, slow_window=2),
        0.5,
        position_sizing=EntryVolatilityPositionSizingSpec(),
    )

    evidence = engine.evidence(frame)
    expected_volatility = pd.Series(np.log(close)).diff().rolling(20).std(ddof=0) * np.sqrt(365)
    expected_entry_weight = min(0.5, 0.20 / expected_volatility.iloc[21])

    assert evidence.loc[21, "realized_volatility"] == pytest.approx(expected_volatility.iloc[21])
    assert evidence.loc[21, "entry_weight"] == pytest.approx(expected_entry_weight)
    assert evidence.loc[21:23, "target"].tolist() == pytest.approx([expected_entry_weight] * 3)
    assert evidence.loc[24, "target"] == 0
    assert evidence.loc[22, "entry_weight"] != pytest.approx(expected_entry_weight)
    assert set(evidence["position_sizing_family"]) == {"entry_volatility"}


def test_entry_volatility_stays_flat_when_risk_measure_is_unavailable():
    close = np.exp(np.arange(30, dtype=float))
    frame = pd.DataFrame({"close": close})
    engine = RuleSignalEngine(
        SmaStrategySpec(fast_window=1, slow_window=2),
        0.5,
        position_sizing=EntryVolatilityPositionSizingSpec(),
    )

    evidence = engine.evidence(frame)

    assert evidence.loc[:19, "realized_volatility"].isna().all()
    assert evidence["entry_weight"].eq(0).all()
    assert evidence["target"].eq(0).all()


def test_entry_volatility_cannot_change_past_targets_with_future_prices():
    close = 100 * np.exp(np.r_[0, np.cumsum(([-.02, -.10] * 25))])
    frame = pd.DataFrame({"close": close})
    sizing = EntryVolatilityPositionSizingSpec()
    spec = SmaStrategySpec(fast_window=1, slow_window=2)
    before = RuleSignalEngine(spec, 0.5, position_sizing=sizing).evidence(frame)
    changed = frame.copy()
    changed.loc[40:, "close"] *= 10
    after = RuleSignalEngine(spec, 0.5, position_sizing=sizing).evidence(changed)

    pd.testing.assert_frame_equal(before.iloc[:40], after.iloc[:40])


def test_rsi_bollinger_uses_entry_and_exit_state_machine():
    frame = pd.DataFrame({"close": [100., 100, 90, 89, 100]})
    spec = RsiBollingerStrategySpec(
        rsi_window=2, bollinger_window=3, bollinger_stddev=.5,
        entry_rsi=40, exit_rsi=60,
    )
    engine = RuleSignalEngine(spec, 0.5)

    assert engine.generate({"BTCUSDT": frame})["BTCUSDT"].tolist() == [0, 0, .5, .5, 0]
    evidence = engine.evidence(frame)
    assert {"rsi", "bollinger_mid", "bollinger_lower", "entry", "exit", "target"}.issubset(evidence)


def test_donchian_compares_close_with_prior_channels_only():
    frame = pd.DataFrame({
        "high": [10., 11, 12, 15, 14, 13],
        "low": [8., 9, 10, 13, 12, 8],
        "close": [9., 10, 11, 16, 13, 7],
    })
    spec = DonchianStrategySpec(entry_window=3, exit_window=2, atr_window=2)
    engine = RuleSignalEngine(spec, 0.5)

    assert engine.generate({"BTCUSDT": frame})["BTCUSDT"].tolist() == [0, 0, 0, .5, .5, 0]
    evidence = engine.evidence(frame)
    assert evidence.loc[3, "donchian_entry"] == 12
    assert evidence.loc[5, "donchian_exit"] == 12
    assert evidence["atr"].notna().sum() > 0


@pytest.mark.parametrize("spec", [
    RsiBollingerStrategySpec(), DonchianStrategySpec(),
])
def test_stateful_strategies_are_causal(spec):
    frame = pd.DataFrame({
        "open": range(1, 101), "high": range(2, 102), "low": range(100),
        "close": range(1, 101),
    }, dtype=float)
    full = RuleSignalEngine(spec, .5).generate({"BTCUSDT": frame})["BTCUSDT"]
    for end in (20, 40, 80):
        partial = RuleSignalEngine(spec, .5).generate({"BTCUSDT": frame.iloc[:end]})["BTCUSDT"]
        pd.testing.assert_series_equal(partial, full.iloc[:end])
