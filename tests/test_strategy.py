import pandas as pd
import pytest

from lab.contracts import DonchianStrategySpec, RsiBollingerStrategySpec, SmaStrategySpec
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
