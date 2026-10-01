"""Recorded-Jev replay against subsequent quotes and declared funding settlements."""

from decimal import Decimal

from intraday.contracts import Direction
from intraday.portfolio_coordinator import ParentPortfolioCoordinator, ParentPortfolioPolicy
from intraday.scoped_gate import ScopedEntryGate


def _execute_decision(config, data, book, quote, recorded, gate, *, closed):
    decision, at, mark = recorded.decision, quote.at, quote.mark
    trace = {"decision_id": decision.decision_id, "provenance_id": recorded.provenance_id}
    if closed:
        book._event(at, "decision_ignored", "exit_quote_no_reentry", mark, **trace)
        return
    if not recorded.features_valid:
        book.deny(at, mark, "decision_features_incomplete", **trace)
        return
    if not 0 <= (at-decision.created_at).total_seconds() <= 45:
        book.deny(at, mark, "decision_stale", **trace)
        return
    if decision.direction == Direction.TAKE_PROFIT:
        if book.quantity:
            book.close(at, quote.bid if book.quantity > 0 else quote.ask, "take_profit")
        else:
            book._event(at, "decision_ignored", "no_position_to_close", mark, **trace)
        return
    if decision.direction == Direction.HOLD or book.quantity:
        book._event(at, "decision_ignored", "hold" if not book.quantity else "position_already_open", mark, **trace)
        return
    if book.halted:
        book.deny(at, mark, "portfolio_loss_limit", **trace)
        return
    if (quote.ask-quote.bid)/((quote.ask+quote.bid)/2)*10000 > 10:
        book.deny(at, mark, "spread_limit", **trace)
        return
    if not recorded.reference_price.is_finite() or recorded.reference_price <= 0 or abs(mark/recorded.reference_price-1) > Decimal(".01"):
        book.deny(at, mark, "price_dislocation", **trace)
        return
    equity = book.equity(mark)
    target = min(config.capital*Decimal(".20"), equity*Decimal(".20"),
                 equity*Decimal(".10")*config.leverage)
    if target <= 0:
        book.deny(at, mark, "insufficient_equity", **trace)
        return
    authorization = gate.perp_entry(book.state(at, mark), decision, data.rule.parameters,
        projected_isolated_margin_pct=float(target/config.leverage/equity))
    if not authorization.allowed:
        book.deny(at, mark, ",".join(authorization.reason_codes), **trace)
        return
    side = 1 if decision.direction in {Direction.BUY, Direction.STRONG_BUY} else -1
    price = quote.ask if side == 1 else quote.bid
    # The cap remains notional, not margin multiplied by leverage.
    target = min(target, abs(Decimal(str(authorization.target_notional))))
    if book.enter(at, price, target, side=side,
                  stop_distance=Decimal(str(data.rule.parameters.stop_distance_pct))):
        book.events[-1].update(trace)


def replay_perp(config, data, book, limitations):
    gate = ScopedEntryGate(ParentPortfolioCoordinator(ParentPortfolioPolicy(
        initial_equity=float(config.capital), leverage=config.leverage)))
    funding = config.profile.funding
    settlements = tuple(row for row in funding.settlements if config.start <= row.at < config.end) if funding else ()
    decision_index = funding_index = 0
    last = None
    if not book.funding_complete:
        limitations.add("funding_coverage_incomplete")
        limitations.add("risk_limits_use_known_costs_only")
    if not data.decisions:
        limitations.add("no_verified_recorded_decisions")
    quotes = [q for q in data.quotes if config.start <= q.at < config.end]
    if any(not q.fresh or not 0 <= (q.at-q.event_time).total_seconds() <= 45 for q in quotes):
        limitations.add("stale_quotes_not_used_for_fills")
    quotes = [q for q in quotes if q.fresh and 0 <= (q.at-q.event_time).total_seconds() <= 45]
    for quote in quotes:
        while funding_index < len(settlements) and settlements[funding_index].at <= quote.at:
            row = settlements[funding_index]
            book.advance_day(row.at)
            book.settle_funding(row.at, row.rate, row.mark)
            book.observe(row.at, row.mark)
            reason = book.guard_reason(row.mark)
            if reason:
                book.halt(row.at, row.mark, reason)
            funding_index += 1
        if last and (quote.at-last.at).total_seconds() > 45:
            limitations.add("perp_quote_history_gap")
        elif last is None and (quote.at-config.start).total_seconds() > 45:
            limitations.add("price_history_starts_after_window")
        last = quote
        book.observe(quote.at, quote.mark)
        reason = book.guard_reason(quote.mark)
        if reason:
            book.halt(quote.at, quote.mark, reason)
        closed = False
        price = quote.bid if book.quantity > 0 else quote.ask
        if book.quantity and book.halted:
            book.close(quote.at, price, book.halt_reason)
            closed = True
        elif book.quantity and ((book.quantity > 0 and quote.mark <= book.stop) or
                                (book.quantity < 0 and quote.mark >= book.stop)):
            book.close(quote.at, price, "protective_stop")
            closed = True
        ready = []
        while decision_index < len(data.decisions) and data.decisions[decision_index].available_at < quote.at:
            ready.append(data.decisions[decision_index])
            decision_index += 1
        # Match live latest-decision selection; no multiple actions on one quote.
        for old in ready[:-1]:
            book._event(quote.at, "decision_ignored", "superseded_before_next_quote", quote.mark,
                        decision_id=old.decision.decision_id)
        if ready:
            _execute_decision(config, data, book, quote, ready[-1], gate, closed=closed)
        book.observe(quote.at, quote.mark)
    if last:
        if (config.end-last.at).total_seconds() > 45:
            limitations.add("price_history_ends_before_window")
        if book.quantity:
            book.close(last.at, last.bid if book.quantity > 0 else last.ask, "window_end")
            book.observe(last.at, last.mark)
    else:
        limitations.add("no_valid_perp_quotes")
    for missing in data.decisions[decision_index:]:
        book.deny(missing.available_at, book.last_mark or missing.reference_price,
                  "no_quote_after_decision", decision_id=missing.decision.decision_id)
