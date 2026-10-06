from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.replay_v2.mixed_book import MixedBook, MixedConfig


START = datetime(2026, 9, 30, 4, tzinfo=timezone.utc)
SPOT = {s: Decimal(100) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "ZECUSDT")}


def config(**changes):
    return MixedConfig(**{"start": START, "end": START + timedelta(hours=4), **changes})


def marked_book(**changes):
    book = MixedBook(config(**changes))
    book.perp_marks = {"BTCUSDT": Decimal(100), "ETHUSDT": Decimal(100)}
    return book


def test_spot_and_perp_same_coin_have_separate_positions_and_shared_cash():
    book = marked_book()
    book.enter_batch(START, SPOT, {s: Decimal(1) for s in SPOT})
    assert book.positions["BTCUSDT"].quantity == Decimal("2.4")
    assert book.cash == Decimal("399.1")
    assert book.enter_perp("BTCUSDT", START, SPOT, Decimal(100), 1, Decimal(".02"))
    assert book.perps["BTCUSDT"].quantity == Decimal("1.49865")  # equity after Spot costs
    assert book.locked_margin == Decimal("49.955")
    assert book.cash > 398 and book.free_cash < 350
    assert book.positions["BTCUSDT"].quantity == Decimal("2.4")


@pytest.mark.parametrize("side,exit_price,rate", [(1, 110, ".001"), (-1, 90, ".001"), (-1, 110, "-.001")])
def test_perp_pnl_funding_and_margin_release_reconcile(side, exit_price, rate):
    book = marked_book()
    book.enter_perp("BTCUSDT", START, SPOT, Decimal(100), side, Decimal(".02"))
    book.settle_funding("BTCUSDT", START, Decimal(rate), Decimal(100))
    assert book.locked_margin == 50
    book.close_perp("BTCUSDT", START, Decimal(exit_price), "test")
    assert book.locked_margin == 0
    assert book.cash == 1000 + sum(t["net_pnl"] for t in book.trades)
    assert book.trades[0]["market"] == "perp"
    assert book.trades[0]["funding_paid"] == Decimal(side) * Decimal("1.5") * 100 * Decimal(rate)


def test_margin_and_reserve_cannot_be_spent_again():
    book = marked_book()
    book.enter_perp("BTCUSDT", START, SPOT, Decimal(100), 1, Decimal(".02"))
    book.cash = Decimal(151)
    book.enter_batch(START, SPOT, {"BTCUSDT": Decimal(1)})
    assert book.free_cash >= min(book.config.capital, book.equity(SPOT)) * Decimal(".10")
    assert book.positions["BTCUSDT"].quantity * 100 < 240


def test_daily_halt_waits_for_actual_flatten_then_resumes_without_resetting_peak():
    book = marked_book()
    book.enter_batch(START, SPOT, {s: Decimal(1) for s in SPOT})
    falling = {s: Decimal(94) for s in SPOT}
    book.enforce_risk(START, falling)
    assert book.halt_reason == "daily_loss_limit"
    assert book.positions["BTCUSDT"].quantity  # risk doesn't fabricate a Spot fill at a tick
    book.advance_day(START + timedelta(days=1))
    assert book.halted
    for s in SPOT:
        book.close(s, START + timedelta(days=1), falling[s], "daily_loss_limit")
    peak = book.peak
    book.maybe_resume(START + timedelta(days=1), falling)
    assert not book.halted and book.peak == peak


def test_daily_resume_does_not_erase_new_day_loss_before_flatten():
    book = marked_book()
    book.enter_batch(START, SPOT, {s: Decimal(1) for s in SPOT})
    book.enforce_risk(START, {s: Decimal(94) for s in SPOT})
    assert book.halt_reason == "daily_loss_limit"
    next_day = START + timedelta(days=1)
    book.advance_day(next_day)
    day_start = book.day_start
    lower = {s: Decimal(88) for s in SPOT}  # Gap loss after midnight, before the fill.
    for s in SPOT:
        book.close(s, next_day, lower[s], "daily_loss_limit")
    book.maybe_resume(next_day, lower)
    assert book.halted and book.day_start == day_start
    assert book.pause_until == datetime(2026, 10, 2, tzinfo=timezone.utc)
    book.observe(next_day, lower)
    book.advance_day(next_day + timedelta(days=1))
    book.maybe_resume(next_day + timedelta(days=1), lower)
    assert not book.halted


def test_terminal_dd_has_priority_and_is_never_resumed():
    book = marked_book()
    book.enter_batch(START, SPOT, {s: Decimal(1) for s in SPOT})
    falling = {s: Decimal(80) for s in SPOT}
    book.enforce_risk(START, falling)
    assert book.halt_reason == "max_drawdown"
    for s in SPOT:
        book.close(s, START, falling[s], "max_drawdown")
    book.advance_day(START + timedelta(days=1))
    book.maybe_resume(START + timedelta(days=1), falling)
    assert book.halted


@pytest.mark.parametrize("changes", [{"perp_cap": "NaN"}, {"perp_cap": ".5"},
    {"reserve": 0}, {"leverage": 0}, {"perp_weights": {"BTCUSDT": ".7"}}])
def test_invalid_mixed_allocations_fail(changes):
    with pytest.raises(ValueError):
        config(**changes)


def spot_inputs(cfg, *, breakout=False, close=100):
    from intraday.replay_v2.contracts import Candle

    bars = []
    for i in range(-31, int((cfg.end-cfg.start).total_seconds()/14400)):
        price = 110 if i == -1 and breakout else 100
        closing = close if i >= 0 else price
        bars.append(Candle(opened_at=cfg.start+timedelta(hours=i*4),
            available_at=cfg.start+timedelta(hours=(i+1)*4), open=100,
            high=max(Decimal("100.1"), price, closing), low=min(Decimal("99.9"), closing), close=closing, volume=1))
    return {s: tuple(bars) for s in cfg.weights}


def perp_inputs(cfg, *, ready=20, confidence=.95, direction="Buy", prices=((0, 100), (20, 100), (30, 100), (40, 100)), funding=True):
    from intraday.contracts import DecisionScope, JevDecision, PerpRuleParameters, ScopedRuleCandidate
    from intraday.replay_v2.contracts import FundingHistory, QuotePoint, RecordedDecision, ReplayDataset

    data, histories = {}, {}
    for s in cfg.perp_weights:
        rule = ScopedRuleCandidate.create(rule_id=s+"-r", parent_rule_id="bootstrap", thesis_id="offline",
            symbol=s, scope=DecisionScope.PERP_INTRADAY, parameters=PerpRuleParameters(),
            created_at=cfg.start, model_ref="archived", prompt_version="test")
        decision = JevDecision(decision_id=s+"-d", tick_id=s+"-t", snapshot_id="source", direction=direction,
            direction_confidence=confidence, regime="Trending Up", toxic_flow=.1, entry_quality=4,
            risk_level="Low", model_ref="archived", created_at=cfg.start)
        quotes = tuple(QuotePoint(at=cfg.start+timedelta(seconds=i), event_time=cfg.start+timedelta(seconds=i),
            snapshot_id=s+str(i), bid=str(Decimal(p)-Decimal(".001")), ask=str(Decimal(p)+Decimal(".001")), mark=str(p))
            for i, p in prices)
        data[s] = ReplayDataset(rule=rule, quotes=quotes, decisions=(RecordedDecision(decision,
            cfg.start+timedelta(seconds=ready), Decimal(100), True, s+"-call"),))
        if funding:
            histories[s] = FundingHistory(symbol=s, source="fixture", coverage_start=cfg.start,
                coverage_end=cfg.end, settlements=())
    return data, histories


def test_joint_replay_waits_for_completion_and_reuses_confidence_gate():
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg)
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    entries = [e for e in result["events"] if e["kind"] == "entry"]
    assert len(entries) == 2 and all(e["at"] == (START+timedelta(seconds=30)).isoformat() for e in entries)
    assert all(e["price"] == "100.001" for e in entries)
    low, _ = perp_inputs(cfg, confidence=.5)
    blocked = simulate_mixed(cfg, spot_inputs(cfg), {}, low, funding)
    assert not blocked["trades"]
    assert blocked["summary"]["blocked_entries"]["low_confidence"] == 2


def test_missing_funding_is_unknown_not_zero_and_control_has_no_perp():
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, _ = perp_inputs(cfg, funding=False)
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, {})
    assert result["summary"]["net_pnl"] is None
    assert result["summary"]["pnl_after_known_costs"] < 0
    control = simulate_mixed(config(trend_filter=False, include_perp=False), spot_inputs(cfg), {}, data, {})
    assert not control["trades"] and control["summary"]["net_pnl"] == 0
    assert control["official_gate_eligible"] is False


def test_spot_future_close_cannot_change_perp_entry_size():
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg)
    a = simulate_mixed(cfg, spot_inputs(cfg, breakout=True, close=100), {}, data, funding)
    b = simulate_mixed(cfg, spot_inputs(cfg, breakout=True, close=200), {}, data, funding)
    entries = lambda r: [e for e in r["events"] if e["kind"] == "entry"]
    assert entries(a) == entries(b)
    assert a["summary"]["pnl_after_known_costs"] != b["summary"]["pnl_after_known_costs"]


def test_mixed_serialized_config_and_input_order_reproduce_result():
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg)
    first = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    restored = MixedConfig.model_validate({k: first["config"][k] for k in MixedConfig.model_fields})
    second = simulate_mixed(restored, dict(reversed(list(spot_inputs(cfg).items()))), {},
        dict(reversed(list(data.items()))), dict(reversed(list(funding.items()))))
    assert first == second


def test_signed_funding_changes_joint_cash_and_contributions():
    from intraday.replay_v2.contracts import FundingSettlement
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg, direction="Sell")
    funding = {s: h.model_copy(update={"settlements": (FundingSettlement(
        at=START+timedelta(seconds=35), rate=".001", mark=100),)}) for s, h in funding.items()}
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    assert result["summary"]["funding_paid_known"] < 0
    assert sum(t["net_pnl"] for t in result["trades"]) == pytest.approx(
        Decimal(str(result["summary"]["pnl_after_known_costs"])))
    assert all(t["side"] == "short" for t in result["trades"])


def test_native_perp_stop_and_no_same_quote_reentry():
    from dataclasses import replace
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg, direction="Sell", prices=((0, 100), (10, 100), (30, 103), (40, 100)), ready=1)
    for s, d in data.items():
        later = replace(d.decisions[0], available_at=START+timedelta(seconds=25),
            decision=d.decisions[0].decision.model_copy(update={"created_at": START+timedelta(seconds=20), "decision_id": "later"}))
        data[s] = replace(d, decisions=d.decisions+(later,))
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    assert len(result["trades"]) == 2
    assert all(t["exit_reason"] == "protective_stop" for t in result["trades"])
    assert sum(e["kind"] == "entry" for e in result["events"]) == 2


def test_quote_gap_is_disclosed_and_no_valid_quotes_is_insufficient():
    from dataclasses import replace
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg, prices=((0, 100), (90, 100)))
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    assert "perp_quote_history_gap:BTCUSDT" in result["limitations"]
    assert result["summary"]["blocked_entries"]["decision_stale"] == 2
    empty = {s: replace(d, quotes=()) for s, d in data.items()}
    assert simulate_mixed(cfg, spot_inputs(cfg), {}, empty, funding)["summary"]["net_pnl"] is None


def test_isolated_collateral_exhaustion_invalidates_full_net_result():
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg, prices=((0, 100), (10, 100), (30, 50), (40, 50)), ready=1)
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    assert "unsupported_liquidation" in result["limitations"]
    assert result["summary"]["net_pnl"] is None


def test_perp_rule_and_funding_must_match_requested_coin():
    from dataclasses import replace
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg)
    bad = {**data, "ETHUSDT": replace(data["ETHUSDT"], rule=data["BTCUSDT"].rule)}
    with pytest.raises(ValueError, match="identity"):
        simulate_mixed(cfg, spot_inputs(cfg), {}, bad, funding)


def test_research_study_runs_four_reports_without_changing_source(tmp_path):
    from intraday.backups import create_backup, verify_backup
    from intraday.replay_v2.mixed_portfolio_research import run_study
    from intraday.store import IntradayStore

    cfg = config(trend_filter=False)
    source = create_backup(IntradayStore(tmp_path/"source.sqlite3").database, tmp_path/"evidence")
    data, funding = perp_inputs(cfg)
    receipt = run_study(source["backup"], tmp_path/"study", start=cfg.start, end=cfg.end,
        loader=lambda store, config: (spot_inputs(config), {}, data), trend_filter=False,
        funding_histories=funding)
    assert len(receipt["results"]) == 4
    assert len({r["result_id"] for r in receipt["results"]}) == 4
    assert len({r["dataset_checksum"] for r in receipt["results"]}) == 1
    assert verify_backup(source["backup"])["sha256"] == source["sha256"]
    assert receipt["source_unchanged"]
    with pytest.raises(FileExistsError):
        run_study(source["backup"], tmp_path/"study", start=cfg.start, end=cfg.end,
            loader=lambda store, config: (spot_inputs(config), {}, data), trend_filter=False,
            funding_histories=funding)


def test_shared_halt_flattens_spot_at_next_open_not_at_perp_tick():
    from intraday.replay_v2.mixed_research import simulate_mixed
    from intraday.replay_v2.contracts import Candle

    cfg = config(trend_filter=False, end=START+timedelta(hours=8))
    candles = spot_inputs(cfg, breakout=True)
    # A known Spot gap loses ~6% of the account while Perp still has a position.
    for s, rows in candles.items():
        prior = rows[-2].model_copy(update={"open": Decimal(100), "low": Decimal(99), "close": Decimal(100)})
        last = Candle(opened_at=START+timedelta(hours=4), available_at=cfg.end,
            open=90, high=91, low=89, close=90, volume=1)
        candles[s] = rows[:-2]+(prior, last)
    data, funding = perp_inputs(cfg, ready=1, prices=((0, 100), (10, 100), (14410, 100), (14440, 100)))
    result = simulate_mixed(cfg, candles, {}, data, funding)
    spot_exits = [t for t in result["trades"] if t["market"] == "spot"]
    perp_exits = [t for t in result["trades"] if t["market"] == "perp"]
    assert all(t["closed_at"] == (START+timedelta(hours=4)).isoformat() for t in spot_exits)
    assert all(t["closed_at"] == (START+timedelta(seconds=14410)).isoformat() for t in perp_exits)
    assert result["summary"]["daily_pause_count"] == 1


def test_halt_at_perp_tick_leaves_spot_pending_until_next_open():
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False, end=START+timedelta(hours=8))
    data, funding = perp_inputs(cfg, ready=1, prices=((0, 100), (10, 100), (30, 80), (40, 80)))
    result = simulate_mixed(cfg, spot_inputs(cfg, breakout=True), {}, data, funding)
    spots = [t for t in result["trades"] if t["market"] == "spot"]
    perps = [t for t in result["trades"] if t["market"] == "perp"]
    assert len(spots) == 5 and len(perps) == 2
    assert all(t["closed_at"] == (START+timedelta(hours=4)).isoformat() for t in spots)
    assert all(t["closed_at"] == (START+timedelta(seconds=30)).isoformat() for t in perps)
    assert all(t["exit_reason"] == "daily_loss_limit" for t in result["trades"])


def test_report_events_remain_chronological_with_unfilled_final_decisions():
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg, prices=((0, 100),))
    result = simulate_mixed(cfg, spot_inputs(cfg, breakout=True), {}, data, funding)
    times = [datetime.fromisoformat(e["at"]) for e in result["events"]]
    assert times == sorted(times)


def test_fill_spread_and_price_dislocation_are_not_bypassed():
    from dataclasses import replace
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg, prices=((0, 100), (30, 102), (40, 102)))
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    assert result["summary"]["blocked_entries"]["price_dislocation"] == 2
    wide = {s: replace(d, quotes=tuple(q.model_copy(update={"bid": Decimal(99), "ask": Decimal(103)})
                                     for q in d.quotes)) for s, d in data.items()}
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, wide, funding)
    assert result["summary"]["blocked_entries"]["spread_limit"] == 2


def test_latest_decision_wins_and_take_profit_closes_without_flip():
    from dataclasses import replace
    from intraday.contracts import Direction
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg, ready=1, prices=((0, 100), (10, 100), (30, 100), (40, 100)))
    for s, d in data.items():
        first = d.decisions[0]
        values = []
        for second, direction in ((2, "Buy"), (20, "Take Profit")):
            decision = first.decision.model_copy(update={"created_at": START+timedelta(seconds=second),
                "direction": Direction(direction), "decision_id": s+str(second)})
            values.append(replace(first, decision=decision, available_at=START+timedelta(seconds=second+1)))
        data[s] = replace(d, decisions=(first, *values))
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    assert len(result["trades"]) == 2 and all(t["exit_reason"] == "take_profit" for t in result["trades"])
    assert sum(e["reason"] == "superseded_before_next_quote" for e in result["events"]) == 2


def test_same_timestamp_perp_take_profit_precedes_new_spot_entries():
    from dataclasses import replace
    from intraday.contracts import Direction
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False, end=START+timedelta(hours=8))
    candles = spot_inputs(cfg)
    for s, rows in candles.items():
        candles[s] = rows[:-2]+(rows[-2].model_copy(update={"high": Decimal(110), "close": Decimal(110)}), rows[-1])
    data, funding = perp_inputs(cfg, ready=1, prices=((0, 100), (10, 100), (14400, 100), (14430, 100)))
    for s, d in data.items():
        last = replace(d.decisions[0], available_at=START+timedelta(seconds=14399),
            decision=d.decisions[0].decision.model_copy(update={"created_at": START+timedelta(seconds=14398),
                "direction": Direction.TAKE_PROFIT, "decision_id": "tp-"+s}))
        data[s] = replace(d, decisions=d.decisions+(last,))
    result = simulate_mixed(cfg, candles, {}, data, funding)
    events = [e for e in result["events"] if e["at"] == (START+timedelta(hours=4)).isoformat()]
    actions = [e["kind"] for e in events if e["kind"] in {"entry", "exit"}]
    assert actions[:2] == ["exit", "exit"]
    assert "entry" in actions[2:]


def test_simultaneous_funding_is_net_settled_before_shared_risk_check():
    from dataclasses import replace
    from intraday.contracts import Direction
    from intraday.replay_v2.contracts import FundingSettlement
    from intraday.replay_v2.mixed_research import simulate_mixed

    cfg = config(trend_filter=False)
    data, funding = perp_inputs(cfg, ready=1)
    eth = data["ETHUSDT"]
    data["ETHUSDT"] = replace(eth, decisions=(replace(eth.decisions[0],
        decision=eth.decisions[0].decision.model_copy(update={"direction": Direction.SELL})),))
    funding = {s: h.model_copy(update={"settlements": (FundingSettlement(
        at=START+timedelta(seconds=35), rate=".25", mark=100),)}) for s, h in funding.items()}
    result = simulate_mixed(cfg, spot_inputs(cfg), {}, data, funding)
    assert result["summary"]["daily_pause_count"] == 0
    assert result["summary"]["funding_paid_known"] < 1


def test_collection_failure_publishes_unknown_funding_not_fake_zero(tmp_path):
    from intraday.backups import create_backup
    from intraday.replay_v2.mixed_portfolio_research import run_study
    from intraday.store import IntradayStore

    cfg = config(trend_filter=False)
    source = create_backup(IntradayStore(tmp_path/"db.sqlite3").database, tmp_path/"source")
    data, _ = perp_inputs(cfg)
    def unavailable(*args):
        raise OSError("public history unavailable")
    receipt = run_study(source["backup"], tmp_path/"study", start=cfg.start, end=cfg.end,
        loader=lambda store, config: (spot_inputs(config), {}, data), trend_filter=False,
        collect_funding=True, fetcher=unavailable)
    assert all(r["status"] == "unavailable" for r in receipt["collection"])
    assert all(r["summary"]["net_pnl"] is None for r in receipt["results"][:2])
    assert all(r["summary"]["net_pnl"] == 0 for r in receipt["results"][2:])
