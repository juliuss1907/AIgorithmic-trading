"""Research metrics and deterministic input identity, separate from v1 gates."""

from collections import Counter
from decimal import Decimal
import hashlib
import json
from math import ceil


EVALUATOR_VERSION = "replay-v2.1"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def journal_hash(rows):
    """SHA256 of a JSONL journal exactly as publish_report writes it."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update((encoded(row)+'\n').encode())
    return digest.hexdigest()


def dataset_fingerprint(data):
    setup2 = ({"profile_candles": [[b.opened_at.isoformat(), str(b.open), str(b.high), str(b.low), str(b.close),
                                    str(b.volume)] for b in data.profile_candles],
               "indicator_anchor": data.indicator_anchor.isoformat()} if data.indicator_anchor else {})
    return fingerprint({**setup2, "rule": data.rule.model_dump(mode="json"),
        "candles": [c.model_dump(mode="json") for c in data.candles],
        "quotes": [q.model_dump(mode="json") for q in data.quotes],
        "decisions": [{"decision": d.decision.model_dump(mode="json"),
            "available_at": d.available_at.isoformat(), "reference_price": str(d.reference_price),
            "features_valid": d.features_valid, "provenance_id": d.provenance_id} for d in data.decisions],
        "limitations": data.limitations})


def build_result(config, data, book, limitations):
    equity = book.equity(book.last_mark) if book.last_mark else config.capital
    known_pnl = equity-config.capital
    daily = {}
    for point in book.curve:
        daily[point["at"][:10]] = Decimal(point["equity_known"])
    previous, returns = config.capital, []
    for value in daily.values():
        if previous > 0:
            returns.append((value/previous-1)*100)
        previous = value
    tail = sorted(returns)[:max(1, ceil(len(returns)*.05))]
    reasons = Counter(event["reason"] for event in book.events if event["kind"] == "entry_blocked")
    exits = Counter(trade["exit_reason"] for trade in book.trades)
    config_payload = config.model_dump(mode="json")
    digest = dataset_fingerprint(data)
    version = EVALUATOR_VERSION if config.profile.version == "1" else "replay-v2.2"
    result_id = fingerprint({"evaluator": version, "config": config_payload, "dataset": digest})
    result = {"schema_version": "2", "evaluator_version": version,
        "result_id": result_id, "research_only": True, "activation_allowed": False,
        "status": "insufficient_data" if not book.curve else "limited" if limitations else "complete",
        "config": config_payload,
        "inputs": {"dataset_checksum": digest, "config_checksum": fingerprint(config_payload),
            "rule_id": data.rule.rule_id, "rule_content_hash": data.rule.content_hash,
            "rule_parameters": data.rule.parameters.model_dump(mode="json"),
            "candles": len(data.candles), "quotes": len(data.quotes), "recorded_decisions": len(data.decisions)},
        "summary": {"initial_capital": float(config.capital), "final_equity_known": float(equity),
            "pnl_after_known_costs": float(known_pnl),
            "net_pnl": float(known_pnl) if book.funding_complete and book.curve else None,
            "return_after_known_costs_pct": float(known_pnl/config.capital*100),
            "net_return_pct": float(known_pnl/config.capital*100) if book.funding_complete and book.curve else None,
            "realized_gross_pnl": float(book.realized),
            "unrealized_pnl": float(book.unrealized(book.last_mark)) if book.last_mark else 0,
            "execution_cost_known": float(book.costs), "funding_paid_known": float(book.funding),
            "funding_complete": book.funding_complete, "max_drawdown_known_pct": float(book.max_drawdown*100),
            "daily_expected_shortfall_known_pct": float(sum(tail)/len(tail)) if tail else None,
            "daily_return_samples": len(returns), "max_exposure_pct": float(book.max_exposure*100),
            "max_isolated_margin_pct": float(book.max_margin*100), "closed_trades": len(book.trades),
            "open_quantity": str(book.quantity), "blocked_entries": dict(sorted(reasons.items())),
            "exit_reasons": dict(sorted(exits.items())), "halted": book.halted, "halt_reason": book.halt_reason},
        "limitations": sorted(set(limitations)),
        "methodology": {"window": "continuous, flat start; terminal window_end close at last valid price",
            "costs": "assumed combined fees/slippage per fill; recorded quote spread is additional",
            "funding": "declared offline settlement coverage, never inferred from current funding rate",
            "risk_limits": ({"daily_loss_pct": 1.5, "max_drawdown_pct": 8, "spot_stop_pct": 10} if book.policy is None
                            else {"daily_loss_pct": float(book.daily_loss*100), "max_drawdown_pct": float(book.max_dd*100),
                                  "stop": "ATR14x3 trailing, capped 10% from entry",
                                  "entry_fraction_pct": float(book.entry_fraction*100)}),
            "unsupported": ["order_book_replay", "partial_fills", "API_failures", "exact_liquidation",
                            "multi_coin_portfolio", "activation_gate"]},
        "v1_reference": {"evaluation": data.v1_reference,
            "comparison": "reference only; v1 folds/outcomes are not comparable account replay metrics"},
        "equity_curve": book.curve, "trades": book.trades, "events": book.events}
    if config.profile.version == "2":
        result["summary"].update(exchange_fee_known=float(book.exchange_fees),
                                  slippage_cost_known=float(book.slippage_costs))
        result["methodology"]["costs"] = "published regular-user fee plus assumed cash-charged slippage per fill; quote spread implicit and funding separate"
    return result
