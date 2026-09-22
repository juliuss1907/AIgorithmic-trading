from datetime import datetime, timezone

import pytest

from intraday.contracts import FeatureSnapshot, GateDecision, PositionSnapshot
from intraday.paper import PaperPortfolio
from intraday.risk import HardRiskPolicy


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def snapshot(bid=99_990, ask=100_010):
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=NOW,
        built_at=NOW,
        bid=bid,
        ask=ask,
        features={"price": (bid + ask) / 2, "mark_price": 100_000},
        freshness={"book": True, "candles": True},
    )


def gate(target, notional):
    return GateDecision(
        gate_id=f"gate-{target}",
        decision_id="decision-1",
        rule_id="rule-v1",
        outcome="authorized" if target else "reduce_only",
        target_tranches=target,
        authorized_notional=notional,
        reason_codes=(),
        evaluated_at=NOW,
    )


def test_two_tranche_long_uses_no_more_than_20_percent_initial_margin():
    portfolio = PaperPortfolio(initial_equity=10_000, policy=HardRiskPolicy())

    fill = portfolio.apply(gate(2, 5_000), snapshot(), now=NOW)
    position = portfolio.position(snapshot().features["mark_price"])

    assert fill is not None
    assert position.tranches == 2
    assert position.notional <= 5_000
    assert position.isolated_margin <= 2_000
    assert abs(position.quantity) > 0


def test_opposite_transition_closes_and_realizes_pnl_without_flip():
    portfolio = PaperPortfolio(initial_equity=10_000, policy=HardRiskPolicy())
    portfolio.apply(gate(1, 2_500), snapshot(), now=NOW)

    fill = portfolio.apply(gate(0, 0), snapshot(bid=101_000, ask=101_020), now=NOW)

    assert fill.side == "sell"
    assert fill.reduce_only is True
    assert portfolio.position(101_010).tranches == 0
    assert portfolio.realized_pnl > 0


def test_repeating_same_gate_is_idempotent():
    portfolio = PaperPortfolio(initial_equity=10_000, policy=HardRiskPolicy())
    decision = gate(1, 2_500)

    first = portfolio.apply(decision, snapshot(), now=NOW)
    repeated = portfolio.apply(decision, snapshot(), now=NOW)

    assert repeated == first
    assert portfolio.position(100_000).tranches == 1


def test_position_model_rejects_impossible_three_tranche_state():
    with pytest.raises(ValueError):
        PositionSnapshot(
            tranches=3,
            quantity=1,
            entry_price=100_000,
            mark_price=100_000,
            notional=100_000,
            leverage=3,
            isolated_margin=33_333,
            maintenance_margin=400,
            liquidation_price=70_000,
            liquidation_buffer=0.3,
            funding=0,
            unrealized_pnl=0,
        )
