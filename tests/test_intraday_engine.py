from datetime import datetime, timezone

from intraday.contracts import Direction, FeatureSnapshot
from intraday.engine import IntradayEngine
from intraday.providers import StubDecisionProvider
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 21, 12, 0, 2, tzinfo=timezone.utc)


def snapshot():
    return FeatureSnapshot.create(
        symbol="BTCUSDT",
        event_time=NOW,
        built_at=NOW,
        bid=99_990,
        ask=100_010,
        features={"price": 100_000, "mark_price": 100_000, "rsi14": 55},
        freshness={"book": True, "candles": True, "funding": True},
    )


def test_stub_tick_runs_end_to_end_and_is_idempotent(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    engine = IntradayEngine(
        store,
        StubDecisionProvider(direction=Direction.STRONG_BUY),
        initial_equity=10_000,
    )

    first = engine.run_tick(snapshot(), now=NOW)
    repeated = engine.run_tick(snapshot(), now=NOW)

    assert first.decision.direction == Direction.STRONG_BUY
    assert first.gate.outcome == "authorized"
    assert first.fill is not None
    assert first.position.tranches == 2
    assert repeated.decision.decision_id == first.decision.decision_id
    assert repeated.fill.fill_id == first.fill.fill_id
    assert store.counts() == {"snapshots": 1, "decisions": 1, "gates": 1, "fills": 1}


def test_engine_restart_restores_the_persisted_portfolio(tmp_path):
    database = tmp_path / "intraday.sqlite"
    first = IntradayEngine(
        IntradayStore(database), StubDecisionProvider(direction=Direction.BUY), initial_equity=10_000
    )
    first.run_tick(snapshot(), now=NOW)

    restarted = IntradayEngine(
        IntradayStore(database), StubDecisionProvider(direction=Direction.HOLD), initial_equity=10_000
    )

    assert restarted.portfolio.position(100_000).tranches == 1
    assert restarted.portfolio.quantity == first.portfolio.quantity
    assert restarted.last_entry_at == NOW


def test_engine_restart_restores_news_pause_and_halt_state(tmp_path):
    database = tmp_path / "intraday.sqlite"
    first = IntradayEngine(
        IntradayStore(database), StubDecisionProvider(direction=Direction.HOLD), initial_equity=10_000
    )
    first.pause_entries("operator_pause", now=NOW)

    restarted = IntradayEngine(
        IntradayStore(database), StubDecisionProvider(direction=Direction.BUY), initial_equity=10_000
    )
    result = restarted.run_tick(snapshot(), now=NOW)

    assert restarted.halted is True
    assert result.gate.outcome == "hold"
    assert "operator_pause" in result.gate.reason_codes


def test_provider_failure_fails_closed_and_is_audited(tmp_path):
    class FailingProvider:
        model_ref = "broken/provider"

        def decide(self, snapshot, tick_id, now):
            raise TimeoutError("provider secret or response must not leak")

    store = IntradayStore(tmp_path / "intraday.sqlite")
    result = IntradayEngine(store, FailingProvider(), initial_equity=10_000).run_tick(
        snapshot(), now=NOW
    )

    assert result.decision.direction == Direction.HOLD
    assert result.decision.model_ref == "fallback/hold-v1"
    assert result.gate.outcome == "hold"
    assert result.fill is None
    assert "provider_failure" in result.gate.reason_codes
    assert "secret" not in str(store.list_decisions())


def test_engine_audits_cross_venue_shadow_without_applying_it(tmp_path):
    original = snapshot()
    enriched = FeatureSnapshot.create(
        symbol=original.symbol,
        event_time=original.event_time,
        built_at=original.built_at,
        bid=original.bid,
        ask=original.ask,
        features={
            **original.features,
            "xv_coverage_score": 1,
            "xv_mark_dislocation_bps": 0.5,
            "hl_return_30s_pct": 0.02,
            "hl_book_imbalance_10bps": 0.2,
        },
        freshness=original.freshness,
    )
    engine = IntradayEngine(
        IntradayStore(tmp_path / "intraday.sqlite"),
        StubDecisionProvider(direction=Direction.BUY),
        initial_equity=10_000,
        cross_venue_mode="shadow",
    )

    result = engine.run_tick(enriched, now=NOW)

    assert result.gate.cross_venue_mode == "shadow"
    assert result.gate.cross_venue_status == "confirming"
    assert result.gate.cross_venue_applied is False
    assert result.gate.entry_quality_adjustment == 0.5
