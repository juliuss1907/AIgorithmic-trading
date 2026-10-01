from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.replay_v2.contracts import ExecutionProfile, ReplayConfig, binance_gate_profile
from intraday.replay_v2.ledger import Ledger


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("market,notional,fee,slip", [
    ("spot", "300", ".3", ".15"), ("perp", "200", ".1", ".1"),
])
def test_fee_and_slippage_are_separate_and_debited_once(market, notional, fee, slip):
    config = ReplayConfig(symbol="ETH", market=market, rule_id="r", start=NOW,
                          end=NOW+timedelta(days=1), profile=binance_gate_profile())
    book = Ledger(config)
    assert book.enter(NOW, Decimal(100), Decimal(notional), side=1, stop_distance=Decimal(".01"))
    assert book.exchange_fees == Decimal(fee)
    assert book.slippage_costs == Decimal(slip)
    expected = Decimal(1000)-Decimal(fee)-Decimal(slip)
    if market == "spot":
        expected -= Decimal(notional)
    assert book.cash == expected
    book.close(NOW+timedelta(seconds=30), Decimal(100), "take_profit")
    assert book.cash == Decimal(1000)-2*(Decimal(fee)+Decimal(slip))
    assert book.costs == book.exchange_fees+book.slippage_costs
    assert Decimal(book.trades[0]["exchange_fee"]) == 2*Decimal(fee)
    assert Decimal(book.trades[0]["slippage_cost"]) == 2*Decimal(slip)


def test_legacy_profile_wire_shape_and_defaults_are_unchanged():
    profile = ExecutionProfile()
    assert profile.cost_bps("spot") == 15
    assert profile.cost_bps("perp") == 5
    assert set(profile.model_dump()) == {
        "profile_id", "version", "spot_cost_bps", "perp_cost_bps", "instrument",
        "instrument_observed_at", "instrument_source", "funding",
    }


def test_split_cost_profile_requires_source_metadata_and_rejects_ambiguous_costs():
    with pytest.raises(ValueError):
        ExecutionProfile(version="2")
    with pytest.raises(ValueError):
        ExecutionProfile(spot_fee_bps=10)
    profile = binance_gate_profile()
    assert profile.cost_bps("spot") == 15
    assert profile.cost_bps("perp") == 10
    assert profile.fee_observed_at == NOW
