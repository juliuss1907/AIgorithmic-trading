"""Deterministic joint timeline for native 4h Spot and recorded-Jev Perp."""

from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from intraday.contracts import Direction, SpotRuleParameters
from intraday.portfolio_coordinator import ParentPortfolioCoordinator, ParentPortfolioPolicy, ParentPortfolioState
from intraday.replay_v2.metrics import dataset_fingerprint, fingerprint
from intraday.replay_v2.mixed_book import MixedBook
from intraday.replay_v2.portfolio_book import STOP, ONE, ZERO
from intraday.replay_v2.portfolio_research import WIDTH, validate_inputs
from intraday.scoped_gate import ScopedEntryGate
from intraday.spot_signal import evaluate_donchian


VERSION = "mixed-portfolio-research-v2"
AGE = timedelta(seconds=45)


def gate_state(book, symbol, at, marks):
    """Single-coin signal-gate projection; aggregate capital checks stay in MixedBook."""
    p, price = book.perps[symbol], book.perp_marks[symbol]
    spot, _ = book.exposures(marks)
    unrealized = p.quantity*(price-p.entry_price) if p.quantity else ZERO
    return ParentPortfolioState(initial_equity=float(book.config.capital),
        realized_pnl=float(book.equity(marks)-book.config.capital-unrealized),
        spot_quantity=float(spot/price), spot_entry_price=float(price) if spot else None,
        perp_quantity=float(p.quantity), perp_entry_price=float(p.entry_price) if p.quantity else None,
        mark_price=float(price), spot_price=float(price), perp_mark_price=float(price),
        day_start_equity=float(book.day_start), high_water_mark=float(book.peak),
        entries_paused=book.halted, halt_reason=book.halt_reason, paper_active=True, updated_at=at)


def execute_decision(book, symbol, quote, recorded, data, marks, latest, *, closed):
    at, decision = quote.at, recorded.decision
    trace = {"symbol": symbol, "market": "perp", "decision_id": decision.decision_id,
             "provenance_id": recorded.provenance_id}
    def deny(reason):
        book.event(at, "entry_blocked", reason, **trace)
    if closed:
        book.event(at, "decision_ignored", "exit_quote_no_reentry", **trace)
        return
    if not recorded.features_valid:
        deny("decision_features_incomplete")
        return
    if not timedelta(0) <= at-decision.created_at <= AGE:
        deny("decision_stale")
        return
    p = book.perps[symbol]
    if decision.direction == Direction.TAKE_PROFIT:
        book.close_perp(symbol, at, quote.bid if p.quantity > 0 else quote.ask, "take_profit")
        return
    if decision.direction == Direction.HOLD or p.quantity:
        book.event(at, "decision_ignored", "hold_or_position_open", **trace)
        return
    if book.halted:
        deny("portfolio_loss_limit")
        return
    if any(p.quantity and (s not in latest or at-latest[s].at > AGE) for s, p in book.perps.items()):
        deny("other_open_perp_mark_stale")
        return
    if (quote.ask-quote.bid)/((quote.ask+quote.bid)/2)*10000 > 10:
        deny("spread_limit")
        return
    if abs(quote.mark/recorded.reference_price-ONE) > Decimal(".01"):
        deny("price_dislocation")
        return
    cfg = book.config
    policy = ParentPortfolioPolicy(initial_equity=float(cfg.capital), spot_budget_pct=float(cfg.entry_cap),
        spot_sleeve_target_pct=1, perp_budget_pct=float(cfg.perp_cap),
        perp_sleeve_notional_pct=float(cfg.perp_weights[symbol]),
        max_gross_exposure_pct=float(cfg.entry_cap+cfg.perp_cap),
        max_abs_net_delta_pct=float(cfg.entry_cap+cfg.perp_cap), max_isolated_margin_pct=float(cfg.perp_cap/cfg.leverage),
        leverage=cfg.leverage, daily_loss_limit_pct=float(cfg.daily_loss), max_drawdown_pct=float(cfg.max_drawdown))
    gate = ScopedEntryGate(ParentPortfolioCoordinator(policy))
    authorization = gate.perp_entry(gate_state(book, symbol, at, marks), decision, data.rule.parameters,
        projected_isolated_margin_pct=float((book.locked_margin +
            book.perp_target(symbol, marks)[2]/cfg.leverage)/book.equity(marks)))
    if not authorization.allowed:
        deny(",".join(authorization.reason_codes))
        return
    side = 1 if decision.direction in {Direction.BUY, Direction.STRONG_BUY} else -1
    if book.enter_perp(symbol, at, marks, quote.ask if side == 1 else quote.bid, side,
            Decimal(str(data.rule.parameters.stop_distance_pct)),
            approved_target=abs(Decimal(str(authorization.target_notional)))):
        book.events[-1].update(trace)


def prepare_perp(config, datasets, funding, book, timeline):
    quotes, decisions = {}, {}
    if set(datasets) != set(config.perp_weights):
        raise ValueError("Perp datasets require every configured coin, including for paired controls")
    for s in sorted(datasets):
        data = datasets[s]
        if data.rule.symbol != s or data.rule.scope.value != "perp_intraday" or data.rule.created_at > config.start:
            raise ValueError("Perp rule identity or availability mismatch")
        if any(a.at >= b.at for a, b in zip(data.quotes, data.quotes[1:])):
            raise ValueError("Perp quotes must be unique and chronological")
        quotes[s] = tuple(q for q in data.quotes if config.start <= q.at < config.end and
                         q.fresh and timedelta(0) <= q.at-q.event_time <= AGE)
        decisions[s] = tuple(sorted(data.decisions, key=lambda d: (d.available_at, d.decision.decision_id)))
        if config.include_perp:
            book.limitations.update(data.limitations)
            if not decisions[s]:
                book.limitations.add("no_verified_recorded_decisions:"+s)
            if len(quotes[s]) != len(data.quotes):
                book.limitations.add("stale_quotes_not_used_for_fills:"+s)
            if not quotes[s]:
                book.limitations.add("no_valid_perp_quotes:"+s)
            else:
                if quotes[s][0].at-config.start > AGE or config.end-quotes[s][-1].at > AGE:
                    book.limitations.add("perp_quote_window_incomplete:"+s)
                if any(b.at-a.at > AGE for a, b in zip(quotes[s], quotes[s][1:])):
                    book.limitations.add("perp_quote_history_gap:"+s)
            history = funding.get(s)
            if history is None or history.coverage_start > config.start or history.coverage_end < config.end:
                book.limitations.add("funding_coverage_incomplete:"+s)
            if history:
                if history.symbol != s:
                    raise ValueError("funding belongs to another coin")
                for settlement in history.settlements:
                    if config.start <= settlement.at < config.end:
                        timeline[settlement.at]["funding"].append((s, settlement))
        for q in quotes[s]:
            timeline[q.at]["quotes"].append((s, q))
    return quotes, decisions


def spot_close(book, at, bars, marks):
    marks.update({s: b.close for s, b in bars.items()})
    for s in sorted(bars):
        p = book.positions[s]
        if p.quantity and bars[s].low <= p.entry_price*(ONE-STOP):
            book.close(s, at, p.entry_price*(ONE-STOP), "emergency_stop_detected_at_close")
    book.enforce_risk(at, marks)


def spot_open(book, at, bars, prepared, indexes, trends, marks, *, perp_stale):
    marks.update({s: b.open for s, b in bars.items()})
    book.enforce_risk(at, marks)
    closed, observations = set(), {}
    for s in sorted(bars):
        i = indexes[s][at]
        observations[s] = evaluate_donchian([c.row() for c in prepared[s][i-31:i]],
            SpotRuleParameters(entry_window=30, exit_window=book.config.exit_window, atr_period=14))
        p = book.positions[s]
        reason = (book.halt_reason if book.halted else "emergency_stop_gap"
            if p.quantity and marks[s] <= p.entry_price*(ONE-STOP) else
            "donchian_exit" if observations[s].exit else None)
        if p.quantity and reason:
            book.close(s, at, marks[s], reason)
            closed.add(s)
    book.enforce_risk(at, marks)
    book.maybe_resume(at, marks)
    requests = {}
    for s, obs in observations.items():
        if not obs.entry or s in closed or book.positions[s].quantity:
            continue
        allowed = not book.config.trend_filter or trends[s][1][bisect_right(trends[s][0], at)-1][1]
        if allowed and not perp_stale:
            requests[s] = Decimal(str(obs.size_multiplier))
        else:
            book.event(at, "entry_blocked", "other_open_perp_mark_stale" if perp_stale else "daily_trend_filter",
                       symbol=s, market="spot")
    book.enter_batch(at, marks, requests)
    book.enforce_risk(at, marks)


def simulate_mixed(config, candles, daily, datasets, funding):
    opens, prepared, trends = validate_inputs(config, candles, daily)
    timeline = defaultdict(lambda: {"quotes": [], "funding": [], "open": False, "close": False})
    for at in opens:
        timeline[at]["open"] = True
        timeline[at+WIDTH]["close"] = True
    book = MixedBook(config)
    quotes, decisions = prepare_perp(config, datasets, funding, book, timeline)
    indexes = {s: {c.opened_at: i for i, c in enumerate(prepared[s])} for s in config.weights}
    marks = {s: prepared[s][indexes[s][config.start]].open for s in config.weights}
    latest, cursor = {}, {s: 0 for s in datasets}
    for at, events in sorted(timeline.items()):
        book.advance_day(at)
        for s, row in sorted(events["funding"]):
            book.perp_marks[s] = row.mark
            book.settle_funding(s, at, row.rate, row.mark)
        if events["funding"]:
            book.check_isolated_collateral(at)
            book.enforce_risk(at, marks)
        for s, q in sorted(events["quotes"]):
            book.perp_marks[s], latest[s] = q.mark, q
        if events["close"]:
            bars = {s: prepared[s][indexes[s][at-WIDTH]] for s in config.weights}
            spot_close(book, at, bars, marks)
        book.check_isolated_collateral(at)
        book.enforce_risk(at, marks)
        closed = set()
        for s, q in sorted(events["quotes"]):
            p = book.perps[s]
            stopped = p.quantity and (q.mark <= p.stop if p.quantity > 0 else q.mark >= p.stop)
            if p.quantity and (book.halted or stopped):
                book.close_perp(s, at, q.bid if p.quantity > 0 else q.ask,
                                book.halt_reason if book.halted else "protective_stop")
                closed.add(s)
                book.enforce_risk(at, marks)
        ready_by_symbol, processed = {}, set()
        for s, q in sorted(events["quotes"]):
            ready = []
            while cursor[s] < len(decisions[s]) and decisions[s][cursor[s]].available_at < at:
                ready.append(decisions[s][cursor[s]])
                cursor[s] += 1
            ready_by_symbol[s] = ready
            if config.include_perp:
                for old in ready[:-1]:
                    book.event(at, "decision_ignored", "superseded_before_next_quote", symbol=s, market="perp",
                               decision_id=old.decision.decision_id)
                if ready and ready[-1].decision.direction == Direction.TAKE_PROFIT:
                    execute_decision(book, s, q, ready[-1], datasets[s], marks, latest, closed=s in closed)
                    book.enforce_risk(at, marks)
                    processed.add(s)
        if events["open"]:
            bars = {s: prepared[s][indexes[s][at]] for s in config.weights}
            stale = any(p.quantity and (s not in latest or at-latest[s].at > AGE) for s, p in book.perps.items())
            spot_open(book, at, bars, prepared, indexes, trends, marks, perp_stale=stale)
        book.maybe_resume(at, marks)
        for s, q in sorted(events["quotes"]):
            ready = ready_by_symbol[s]
            if config.include_perp:
                if ready and s not in processed:
                    execute_decision(book, s, q, ready[-1], datasets[s], marks, latest, closed=s in closed)
                book.enforce_risk(at, marks)
                p = book.perps[s]
                if p.quantity and (book.halted or q == quotes[s][-1]):
                    book.close_perp(s, at, q.bid if p.quantity > 0 else q.ask,
                                    book.halt_reason if book.halted else "window_end_last_valid_quote")
                    book.enforce_risk(at, marks)
        if at == config.end:
            for s in sorted(config.weights):
                book.close(s, at, marks[s], "window_end")
            book.enforce_risk(at, marks)
    if config.include_perp:
        for s in datasets:
            for d in decisions[s][cursor[s]:]:
                book.event(d.available_at, "entry_blocked", "no_quote_after_decision", symbol=s, market="perp",
                           decision_id=d.decision.decision_id)
    return mixed_result(config, prepared, daily, datasets, funding, book)


def mixed_result(config, candles, daily, datasets, funding, book):
    if not book.flat:
        raise ValueError("mixed research must end flat")
    book.events.sort(key=lambda e: datetime.fromisoformat(e["at"]))
    net = book.cash-config.capital
    if abs(sum((t["net_pnl"] for t in book.trades), ZERO)-net) > Decimal("1e-18"):
        raise ValueError("mixed PnL does not reconcile to shared cash")
    contributions = {}
    for market, symbols in (("spot", config.weights), ("perp", config.perp_weights)):
        for s in sorted(symbols):
            trades = [t for t in book.trades if t["symbol"] == s and t["market"] == market]
            contributions[market+":"+s] = {"closed_trades": len(trades), **{name:
                float(sum((t[name] for t in trades), ZERO)) for name in
                ("gross_pnl", "exchange_fee", "slippage_cost", "funding_paid", "net_pnl")}}
    data_hash = fingerprint({"candles": {s: [c.row() for c in candles[s]] for s in sorted(candles)},
        "daily": daily, "perp": {s: dataset_fingerprint(datasets[s]) for s in sorted(datasets)},
        "funding": {s: h.model_dump(mode="json") for s, h in sorted(funding.items())}})
    payload = {**config.model_dump(mode="json"), "symbol": "+".join(s.removesuffix("USDT") for s in sorted(config.weights)),
        "market": "spot+perp" if config.include_perp else "spot-control"}
    complete_funding = not any(x.startswith("funding_coverage_incomplete") for x in book.limitations)
    valid = complete_funding and not any(x.startswith(("unsupported_liquidation", "no_valid_perp_quotes",
        "no_verified_recorded_decisions", "unverified_recorded_decisions")) for x in book.limitations)
    return {"schema_version": "2", "evaluator_version": VERSION,
        "result_id": fingerprint({"version": VERSION, "config": payload, "data": data_hash}),
        "research_only": True, "activation_allowed": False, "official_gate_eligible": False,
        "status": "limited" if book.limitations else "complete", "config": payload,
        "inputs": {"dataset_checksum": data_hash, "config_checksum": fingerprint(payload),
            "perp_rules": {s: {"rule_id": d.rule.rule_id, "content_hash": d.rule.content_hash,
                "parameters": d.rule.parameters.model_dump(mode="json"), "quotes": len(d.quotes),
                "verified_decisions": len(d.decisions)} for s, d in sorted(datasets.items())}},
        "summary": {"initial_capital": float(config.capital), "final_equity_known": float(book.cash),
            "pnl_after_known_costs": float(net), "net_pnl": float(net) if valid else None,
            "net_return_pct": float(net/config.capital*100) if valid else None,
            "return_after_known_costs_pct": float(net/config.capital*100),
            "funding_complete": complete_funding, "funding_paid_known": float(book.funding_paid),
            "exchange_fee_known": float(book.fees), "slippage_cost_known": float(book.slippage),
            "max_drawdown_known_pct": float(book.max_drawdown*100), "closed_trades": len(book.trades),
            "max_exposure_pct": float(book.max_exposure*100), "max_perp_notional_pct": float(book.max_perp_exposure*100),
            "max_isolated_margin_pct": float(book.max_margin*100), "contributions": contributions,
            "blocked_entries": dict(sorted(Counter(e["reason"] for e in book.events if e["kind"] == "entry_blocked").items())),
            "daily_pause_count": sum(e["kind"] == "halt" and e["reason"] == "daily_loss_limit" for e in book.events),
            "daily_resume_count": sum(e["kind"] == "resume" for e in book.events),
            "halted": book.halted, "halt_reason": book.halt_reason, "economic_check_only": "not_evaluated_short_window"},
        "methodology": {"official_gate_eligible": False, "window": "flat start; no annualization or 24-month claim",
            "risk_sampling": "joint Perp quotes/funding and Spot 4h boundaries; last known Spot mark between boundaries",
            "flatten": "Perp next fresh quote; Spot next 4h open; native stop low-touch detected at close",
            "daily_reset": "next UTC day AND all positions flat; high-water mark preserved",
            "risk_limits": {"daily_loss_pct": float(config.daily_loss*100), "max_drawdown_pct": float(config.max_drawdown*100)},
            "costs": {"spot_fee_bps": 10, "spot_slippage_bps": 5, "perp_fee_bps": 5, "perp_slippage_bps": 5},
            "allocation": "notional caps; isolated margin locked; reserve enforced for new entries; no rebalance or transfer"},
        "limitations": sorted(book.limitations | {"short_window_not_profitability_evidence", "research_only_not_activation_gate",
            "spot_between_4h_marks_unobserved", "spot_jev_filter_not_replayed", "llm_not_called",
            "assumed_cash_charged_slippage", "ideal_fractional_fills_no_partial_fills_or_historical_filters",
            "no_exact_liquidation_or_verified_historical_demo_execution"}),
        "v1_reference": None, "equity_curve": book.curve, "trades": book.trades, "events": book.events}


def perp_diagnostics(datasets):
    results = {}
    for s, data in sorted(datasets.items()):
        entries = [d.decision for d in data.decisions
                   if d.decision.direction not in {Direction.HOLD, Direction.TAKE_PROFIT}]
        gaps = [(b.at-a.at).total_seconds() for a, b in zip(data.quotes, data.quotes[1:]) if b.at-a.at > AGE]
        results[s] = {"entry_signals": len(entries), "confidence_threshold": data.rule.parameters.confidence_threshold,
            "maximum_entry_confidence": max((d.direction_confidence for d in entries), default=None),
            "entry_signals_at_threshold": sum(d.direction_confidence >= data.rule.parameters.confidence_threshold for d in entries),
            "quote_gaps_over_45s": len(gaps), "maximum_quote_gap_seconds": max(gaps, default=0)}
    return results
