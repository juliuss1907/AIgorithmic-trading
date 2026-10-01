"""Per-pair operator control; reads never imply ownership or activation."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from pydantic import Field
from intraday.execution.contracts import ExecutionModel, ExecutionUnavailable


class LeveragePreview(ExecutionModel):
    account: str = Field(pattern=r"^binance:demo:[A-Za-z0-9_-]{1,64}$")
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,24}$")
    target: int = Field(strict=True, ge=1, le=10)
    previous: int = Field(strict=True, ge=1)
    revision: int = Field(strict=True, ge=0)
    route_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime


def route_digest(route):
    return hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest()


def queue_leverage_change(journal, preview, *, request_id, account, route, now):
    """Confirmed requests only: shared by local CLI and the keyless web queue."""
    parsed = LeveragePreview.model_validate(preview)
    digest = hashlib.sha256(parsed.model_dump_json().encode()).hexdigest()
    existing = journal.settings_request(request_id)
    if existing:
        return journal.queue_settings_request(existing, digest)
    if not -2 <= (now-parsed.observed_at).total_seconds() <= 60:
        raise ValueError("leverage confirmation expired; refresh before confirming")
    if parsed.account != account.key or parsed.symbol != route["symbol"] or parsed.route_digest != route_digest(route):
        raise ValueError("leverage confirmation context changed")
    item = {"id": request_id, "account": account.key, "symbol": parsed.symbol,
            "status": "queued", "preview": parsed.model_dump(mode="json"), "created_at": now.isoformat()}
    return journal.queue_settings_request(item, digest)


class PerpController:
    def __init__(self, source, venue, journal=None, *, clock=None):
        self.source, self.venue, self.journal = source, venue, journal
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.account = venue.account_ref.model_copy(update={"market": None})

    def _context(self):
        route = self.source.route()
        if (route["venue"] != "bnb" or route["environment"] != "demo"
                or self.venue.account_ref.venue != "binance" or self.venue.account_ref.environment != "demo"
                or self.venue.account_ref.market != "perp" or route["instrument"] != self.venue.symbol):
            raise ValueError("selected route has no supported Perp control adapter")
        return route

    def information(self):
        route = self._context()
        self.venue.check_clock(now=self.clock())
        snapshot = self.venue.account_snapshot(now=self.clock())
        if snapshot.account != self.venue.account_ref:
            raise ValueError("Perp account identity mismatch")
        positions = []
        for position in snapshot.positions:
            if position.symbol != self.venue.symbol or not position.quantity:
                continue
            expected = Decimal(0)
            if self.journal:
                expected = sum((u.executed_quantity*(1 if i.side == "BUY" else -1)
                    for i,u in self.journal.orders(snapshot.account) if i.symbol == position.symbol), Decimal(0))
            positions.append({"side": "LONG" if position.quantity > 0 else "SHORT",
                "quantity": str(abs(position.quantity)), "entry_price": str(position.entry_price),
                "mark_price": str(position.mark_price),
                "unrealized_pnl": str(position.quantity*(position.mark_price-position.entry_price)),
                "managed_by_aigt": expected == position.quantity})
        portfolio = self.journal.portfolio(self.account) if self.journal else None
        setting = self.journal.perp_setting(self.account, self.venue.symbol) if self.journal else {"leverage": 3, "revision": 0}
        blockers = self._blockers(snapshot, setting["leverage"])
        requests = [r for r in self.journal.settings_requests(self.account) if r["symbol"] == self.venue.symbol] if self.journal else []
        if requests:
            blockers.append("pending leverage request must be reconciled first")
        return {"status": "verified", "symbol": self.venue.symbol, "venue": route["venue"],
            "environment": route["environment"], "market": "USD-M USDT Perpetual",
            "account": self.account.key, "margin_mode": snapshot.margin_mode,
            "actual_leverage": snapshot.leverage, "configured_leverage": setting["leverage"], "setting_revision": setting["revision"],
            "route_digest": route_digest(route), "change_blockers": blockers,
            "leverage_requests": [{"id":r["id"], "status":r["status"], "target":r["preview"]["target"]} for r in requests],
            "positions": positions, "pnl_currency": "USDT", "pnl_basis": "mark_to_entry_excluding_fees_and_funding",
            "open_orders": sum(o.symbol == self.venue.symbol for o in snapshot.open_orders),
            "aigt_state": "not_activated" if not portfolio else "paused" if portfolio["paused"] else "running",
            "observed_at": snapshot.observed_at.isoformat(), "trading_activated": False}

    def _blockers(self, snapshot, target):
        reasons = []
        if not snapshot.can_trade or not snapshot.one_way or not snapshot.single_asset:
            reasons.append("One-way Single-asset trading account required")
        if snapshot.margin_mode.upper() != "ISOLATED":
            reasons.append("Isolated margin required; change margin mode manually")
        if any(p.symbol == self.venue.symbol and p.quantity for p in snapshot.positions) or any(o.symbol == self.venue.symbol for o in snapshot.open_orders):
            reasons.append("pair must be flat with no open orders")
        if self.journal:
            legacy, portfolio = self.journal.control(self.account), self.journal.portfolio(self.account)
            if any(c is not None and c.get("paused") is not True for c in (legacy, portfolio)):
                reasons.append("pause execution portfolio first")
            if legacy is not None and portfolio is None and target != 3:
                reasons.append("migrate legacy BTC execution to multi-route before non-3x leverage")
            for ref in (self.account, self.venue.account_ref):
                orders = [(i,u) for i,u in self.journal.orders(ref) if i.symbol == self.venue.symbol]
                if any(not u.terminal or sum((f.quantity for f in u.fills), Decimal(0)) != u.executed_quantity for i,u in orders):
                    reasons.append("unresolved order requires reconciliation")
                if sum((u.executed_quantity*(1 if i.side == "BUY" else -1) for i,u in orders), Decimal(0)):
                    reasons.append("journal position must be flat and reconciled")
        return list(dict.fromkeys(reasons))

    def preview(self, target):
        if type(target) is not int or not 1 <= target <= 10:
            raise ValueError("leverage must be an integer from 1 to 10")
        info = self.information()
        snapshot = self.venue.account_snapshot(now=self.clock())
        blockers = self._blockers(snapshot, target)
        if blockers:
            raise ValueError("; ".join(blockers))
        if target > self.venue.leverage_limit():
            raise ValueError("leverage exceeds the venue limit")
        return LeveragePreview(account=self.account.key, symbol=self.venue.symbol, target=target,
            previous=snapshot.leverage, revision=info["setting_revision"], route_digest=info["route_digest"],
            observed_at=snapshot.observed_at).model_dump(mode="json")

    def request_change(self, preview, *, request_id):
        if self.journal is None:
            raise ValueError("writable execution journal required after confirmation")
        return queue_leverage_change(self.journal, preview, request_id=request_id,
            account=self.account, route=self._context(), now=self.clock())

    def process(self, request_id):
        with self.journal.lock():
            item = self.journal.settings_request(request_id)
            if not item or item["account"] != self.account.key or item["symbol"] != self.venue.symbol:
                raise ValueError("unknown request for this account/pair")
            if item["status"] in {"verified", "failed"}:
                return item
            preview = LeveragePreview.model_validate(item["preview"])
            # A crash or timeout after a durable claim is reconciled using reads only.
            if item["status"] in {"applying", "unknown"}:
                try:
                    if route_digest(self._context()) != preview.route_digest:
                        raise ValueError("route changed during reconciliation")
                    snapshot = self.venue.account_snapshot(now=self.clock())
                    if snapshot.account != self.venue.account_ref:
                        raise ValueError("account identity mismatch")
                    if (snapshot.leverage == preview.target and snapshot.margin_mode.upper() == "ISOLATED"
                            and snapshot.one_way and snapshot.single_asset and snapshot.can_trade):
                        item.update(status="verified", reason=None)
                        self.journal.save_settings_request(item, now=self.clock(), verified_leverage=preview.target)
                    else:
                        item.update(status="unknown", reason="manual reconciliation required; never resubmitted")
                        self.journal.save_settings_request(item, now=self.clock())
                except (OSError, ValueError, ExecutionUnavailable):
                    item.update(status="unknown", reason="account read unavailable; reconciliation required")
                    self.journal.save_settings_request(item, now=self.clock())
                return item
            try:
                if not -2 <= (self.clock()-preview.observed_at).total_seconds() <= 60:
                    raise ValueError("confirmation expired")
                current = self.preview(preview.target)
                if any(current[k] != item["preview"][k] for k in ("account", "symbol", "previous", "revision", "route_digest")):
                    raise ValueError("confirmation changed; refresh and confirm again")
            except (OSError, ValueError, ExecutionUnavailable):
                item.update(status="failed", reason="preconditions or confirmation changed; refresh and confirm again")
                self.journal.save_settings_request(item, now=self.clock())
                return item
            item.update(status="applying")
            self.journal.save_settings_request(item, now=self.clock())
            try:
                if preview.previous != preview.target:
                    self.venue.set_leverage(preview.target)
                snapshot = self.venue.account_snapshot(now=self.clock())
                if snapshot.account != self.venue.account_ref or snapshot.leverage != preview.target:
                    raise ExecutionUnavailable("settings read-back mismatch")
                item.update(status="verified", reason=None)
                self.journal.save_settings_request(item, now=self.clock(), verified_leverage=preview.target)
            except (OSError, ValueError, ExecutionUnavailable):
                item.update(status="unknown", reason="settings result unknown; read-only reconciliation required")
                self.journal.save_settings_request(item, now=self.clock())
            return item
