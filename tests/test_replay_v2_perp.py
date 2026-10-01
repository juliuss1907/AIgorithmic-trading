from datetime import datetime, timedelta, timezone
from decimal import Decimal

from intraday.contracts import (DecisionScope, Direction, JevDecision, PerpRuleParameters,
                                Regime, RiskLevel, ScopedRuleCandidate)
from intraday.replay_v2.contracts import (ExecutionProfile, FundingHistory, QuotePoint,
    RecordedDecision, ReplayConfig, ReplayDataset)
from intraday.replay_v2.engine import simulate


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def quote(second, price=100, **kw):
    return QuotePoint(at=NOW+timedelta(seconds=second), event_time=NOW+timedelta(seconds=second),
        snapshot_id=f"snapshot-{second}", bid=str(price-.001), ask=str(price+.001), mark=str(price), **kw)


def decision(second=0, *, direction=Direction.BUY, ready=None, confidence=.95):
    value = JevDecision(decision_id=f"d-{second}-{direction.value}", tick_id=f"t-{second}", snapshot_id="source",
        direction=direction, direction_confidence=confidence, regime=Regime.TRENDING_UP,
        toxic_flow=.1, entry_quality=4, risk_level=RiskLevel.LOW, model_ref="archived",
        created_at=NOW+timedelta(seconds=second))
    return RecordedDecision(value, NOW+timedelta(seconds=ready if ready is not None else second+1),
        Decimal(100), True, f"call-{second}")


def inputs(quotes, decisions, **kw):
    config = ReplayConfig(symbol="ETH", market="perp", rule_id="r", start=NOW,
                          end=NOW+timedelta(seconds=200), **kw)
    rule = ScopedRuleCandidate.create(rule_id="r", parent_rule_id="bootstrap", thesis_id="offline",
        symbol="ETHUSDT", scope=DecisionScope.PERP_INTRADAY, parameters=PerpRuleParameters(),
        created_at=NOW, model_ref="operator", prompt_version="test")
    return config, ReplayDataset(rule=rule, quotes=tuple(quotes), decisions=tuple(decisions))


def test_perp_fills_only_after_recorded_decision_completion_and_no_fake_net():
    config, data = inputs([quote(0), quote(20), quote(30), quote(60, 100.5)], [decision(ready=20)])
    report = simulate(config, data)
    entry = next(e for e in report["events"] if e["kind"] == "entry")
    assert entry["at"] == (NOW+timedelta(seconds=30)).isoformat()
    assert entry["price"] == "100.001"
    assert report["summary"]["net_pnl"] is None
    assert report["summary"]["pnl_after_known_costs"] > 0
    assert "funding_coverage_incomplete" in report["limitations"]


def test_perp_short_stop_and_no_reentry_on_close_quote():
    config, data = inputs([quote(0), quote(10), quote(30, 102), quote(60)],
                          [decision(direction=Direction.SELL), decision(20)])
    report = simulate(config, data)
    assert report["trades"][0]["side"] == "short"
    assert report["trades"][0]["exit_reason"] == "protective_stop"
    assert sum(e["kind"] == "entry" for e in report["events"]) == 1


def test_missing_quote_or_stale_decision_never_produces_trade():
    config, data = inputs([quote(0), quote(90)], [decision()])
    report = simulate(config, data)
    assert not report["trades"]
    assert report["summary"]["blocked_entries"]["decision_stale"] == 1
    config, data = inputs([quote(0)], [decision()])
    report = simulate(config, data)
    assert report["summary"]["blocked_entries"]["no_quote_after_decision"] == 1


def test_take_profit_closes_position_and_opposite_signal_does_not_flip():
    config, data = inputs([quote(0), quote(10), quote(30, 100.2), quote(60, 100.5)],
        [decision(), decision(20, direction=Direction.SELL), decision(40, direction=Direction.TAKE_PROFIT)])
    report = simulate(config, data)
    assert len(report["trades"]) == 1
    assert report["trades"][0]["exit_reason"] == "take_profit"
    assert report["trades"][0]["side"] == "long"


def test_recorded_funding_is_signed_and_full_coverage_enables_net():
    history = FundingHistory(symbol="ETH", source="fixture settlements", coverage_start=NOW,
        coverage_end=NOW+timedelta(seconds=201), settlements=[{"at":NOW+timedelta(seconds=20),
                                                             "rate":".001", "mark":"100"}])
    config, data = inputs([quote(0), quote(10), quote(30), quote(60)], [decision()],
                          profile=ExecutionProfile(funding=history))
    report = simulate(config, data)
    expected = Decimal(200)/Decimal("100.001")*100*Decimal(".001")
    assert abs(report["summary"]["funding_paid_known"]-float(expected)) < 1e-12
    assert report["summary"]["net_pnl"] is not None
    assert report["summary"]["funding_complete"]


def test_model_filter_and_spread_dislocation_are_applied():
    config, data = inputs([quote(0), quote(10)], [decision(confidence=.5)])
    assert "low_confidence" in simulate(config, data)["summary"]["blocked_entries"]
    config, data = inputs([quote(0), quote(10, 110)], [decision()])
    assert "price_dislocation" in simulate(config, data)["summary"]["blocked_entries"]
