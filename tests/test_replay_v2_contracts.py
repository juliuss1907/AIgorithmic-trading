from datetime import datetime, timedelta, timezone

import pytest

from intraday.replay_v2.contracts import Candle, ReplayConfig, ExecutionProfile


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def config(**kwargs):
    return ReplayConfig(symbol="DOGE", market="spot", rule_id="research-rule",
                        start=NOW, end=NOW + timedelta(days=1), **kwargs)


def test_replay_configuration_normalizes_dynamic_ticker_and_is_frozen():
    value = config()
    assert value.symbol == "DOGEUSDT"
    assert value.capital == 1000
    assert value.leverage == 3
    assert value.scope.value == "spot_4h"
    with pytest.raises(ValueError):
        value.capital = 2000


@pytest.mark.parametrize("overrides", [{"capital": "NaN"}, {"capital": 10001},
                                     {"leverage": 11}, {"leverage": True}])
def test_replay_rejects_invalid_money_and_leverage(overrides):
    with pytest.raises(ValueError):
        config(**overrides)


def test_replay_requires_aware_nonempty_window():
    with pytest.raises(ValueError):
        ReplayConfig(symbol="BTC", market="perp", rule_id="r", start=NOW, end=NOW)
    with pytest.raises(ValueError):
        ReplayConfig(symbol="BTC", market="perp", rule_id="r", start=NOW.replace(tzinfo=None), end=NOW)


def test_profile_is_only_verified_offline_binance_model_not_hyperliquid():
    assert ExecutionProfile().cost_bps("spot") == 15
    assert ExecutionProfile().cost_bps("perp") == 5
    with pytest.raises(ValueError):
        ExecutionProfile(profile_id="hl-demo")


def test_closed_candle_validates_ohlc_and_four_hour_width():
    opening = int(NOW.timestamp() * 1000)
    row = [opening, "100", "110", "90", "101", "20", opening + 14_400_000 - 1]
    assert Candle.from_row(row).available_at == NOW + timedelta(hours=4)
    with pytest.raises(ValueError):
        Candle.from_row([opening, "100", "99", "90", "101", "20", row[6]])
    with pytest.raises(ValueError):
        Candle.from_row([opening, "NaN", "110", "90", "101", "20", row[6]])


@pytest.mark.parametrize("price", ["1e1000", "1e-1000"])
def test_prices_must_fit_finite_positive_policy_arithmetic(price):
    from intraday.replay_v2.contracts import QuotePoint, FundingSettlement
    with pytest.raises(ValueError):
        Candle(opened_at=NOW, available_at=NOW+timedelta(hours=4),
               open=price, high=price, low=price, close=price, volume=1)
    with pytest.raises(ValueError):
        QuotePoint(at=NOW,event_time=NOW,snapshot_id="s",bid=price,ask=price,mark=price)
    with pytest.raises(ValueError):
        FundingSettlement(at=NOW,rate=".01",mark=price)
