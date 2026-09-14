import pandas as pd
import pytest

from lab.strategy import SignalEngine


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
