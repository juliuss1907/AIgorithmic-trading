import pandas as pd
import pytest

from lab.data import adjust, sessions, validate


@pytest.fixture
def bars():
    return pd.DataFrame(
        {"open": 100., "high": 110., "low": 90., "close": 105., "volume": 1000},
        index=sessions("2025-01-02", "2025-01-10"),
    )


def test_calendar_includes_special_closures(bars):
    assert pd.Timestamp("2025-01-09") not in bars.index
    validate(bars, "2025-01-02", "2025-01-10")


@pytest.mark.parametrize("problem", ["missing", "duplicate", "nan", "bounds"])
def test_bad_data_is_rejected(bars, problem):
    if problem == "missing":
        bars = bars.iloc[1:]
    elif problem == "duplicate":
        bars = pd.concat([bars, bars.iloc[-1:]])
    elif problem == "nan":
        bars.iloc[0, 0] = float("nan")
    else:
        bars.iloc[0, 1] = 50
    with pytest.raises(ValueError):
        validate(bars, "2025-01-02", "2025-01-10")


def test_all_prices_use_the_same_adjustment(bars):
    bars["adj_close"] = bars["close"] * 0.8
    result = adjust(bars)
    assert result.iloc[0]["open"] == 80
    assert result.iloc[0]["close"] == 84
    assert result.iloc[0]["volume"] == 1000
