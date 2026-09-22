"""Deterministic one-way perpetual paper portfolio."""

from __future__ import annotations

import hashlib
from datetime import datetime

from intraday.contracts import FeatureSnapshot, GateDecision, PaperFill, PositionSnapshot
from intraday.risk import HardRiskPolicy


class PaperPortfolio:
    def __init__(
        self,
        initial_equity: float,
        *,
        policy: HardRiskPolicy | None = None,
        taker_fee_bps: float = 5,
        slippage_bps: float = 5,
        maintenance_margin_rate: float = 0.004,
    ):
        if initial_equity <= 0:
            raise ValueError("initial equity must be positive")
        self.initial_equity = float(initial_equity)
        self.policy = policy or HardRiskPolicy()
        self.taker_fee_rate = taker_fee_bps / 10_000
        self.slippage_rate = slippage_bps / 10_000
        self.maintenance_margin_rate = maintenance_margin_rate
        self.quantity = 0.0
        self.tranches = 0
        self.entry_price: float | None = None
        self.realized_pnl = 0.0
        self.fees = 0.0
        self.funding = 0.0
        self._fills: dict[str, PaperFill | None] = {}

    @classmethod
    def from_state(
        cls, state: dict, *, policy: HardRiskPolicy | None = None
    ) -> "PaperPortfolio":
        portfolio = cls(
            state["initial_equity"],
            policy=policy,
            taker_fee_bps=state["taker_fee_bps"],
            slippage_bps=state["slippage_bps"],
            maintenance_margin_rate=state["maintenance_margin_rate"],
        )
        portfolio.quantity = state["quantity"]
        portfolio.tranches = state["tranches"]
        portfolio.entry_price = state["entry_price"]
        portfolio.realized_pnl = state["realized_pnl"]
        portfolio.fees = state["fees"]
        portfolio.funding = state["funding"]
        return portfolio

    def export_state(self) -> dict:
        return {
            "initial_equity": self.initial_equity,
            "taker_fee_bps": self.taker_fee_rate * 10_000,
            "slippage_bps": self.slippage_rate * 10_000,
            "maintenance_margin_rate": self.maintenance_margin_rate,
            "quantity": self.quantity,
            "tranches": self.tranches,
            "entry_price": self.entry_price,
            "realized_pnl": self.realized_pnl,
            "fees": self.fees,
            "funding": self.funding,
        }

    def equity(self, mark_price: float) -> float:
        unrealized = 0.0
        if self.quantity and self.entry_price is not None:
            unrealized = self.quantity * (mark_price - self.entry_price)
        return self.initial_equity + self.realized_pnl + unrealized - self.fees - self.funding

    def position(self, mark_price: float) -> PositionSnapshot:
        if self.tranches == 0:
            return PositionSnapshot.flat(mark_price)
        quantity = self.quantity
        entry = self.entry_price
        notional = abs(quantity) * mark_price
        isolated_margin = notional / self.policy.leverage
        maintenance_margin = notional * self.maintenance_margin_rate
        if quantity > 0:
            liquidation = max(0.0, entry * (
                1 - 1 / self.policy.leverage + self.maintenance_margin_rate
            ))
        else:
            liquidation = entry * (
                1 + 1 / self.policy.leverage - self.maintenance_margin_rate
            )
        buffer = abs(mark_price - liquidation) / mark_price
        unrealized = quantity * (mark_price - entry)
        return PositionSnapshot(
            tranches=self.tranches,
            quantity=quantity,
            entry_price=entry,
            mark_price=mark_price,
            notional=notional,
            isolated_margin=isolated_margin,
            maintenance_margin=maintenance_margin,
            liquidation_price=liquidation,
            liquidation_buffer=buffer,
            funding=self.funding,
            unrealized_pnl=unrealized,
        )

    def apply(
        self,
        gate: GateDecision,
        snapshot: FeatureSnapshot,
        *,
        now: datetime,
    ) -> PaperFill | None:
        if gate.gate_id in self._fills:
            return self._fills[gate.gate_id]
        if gate.outcome == "hold" or gate.target_tranches == self.tranches:
            self._fills[gate.gate_id] = None
            return None

        target = gate.target_tranches
        current = self.tranches
        opening_or_adding = current == 0 or (
            target != 0 and (current > 0) == (target > 0) and abs(target) > abs(current)
        )
        if current and target and (current > 0) != (target > 0):
            raise ValueError("paper engine refuses to flip a position in one gate decision")

        if opening_or_adding:
            target_notional = gate.authorized_notional
            current_mark_notional = abs(self.quantity) * float(snapshot.features.get("mark_price") or 0)
            delta_notional = target_notional - current_mark_notional
            if delta_notional <= 0:
                self._fills[gate.gate_id] = None
                return None
            side = "buy" if target > 0 else "sell"
            reduce_only = False
            top = snapshot.ask if side == "buy" else snapshot.bid
            price = top * (1 + self.slippage_rate if side == "buy" else 1 - self.slippage_rate)
            delta_quantity = delta_notional / price
            signed_delta = delta_quantity if side == "buy" else -delta_quantity
            old_abs_quantity = abs(self.quantity)
            new_abs_quantity = old_abs_quantity + delta_quantity
            self.entry_price = (
                price if old_abs_quantity == 0 else
                (old_abs_quantity * self.entry_price + delta_quantity * price) / new_abs_quantity
            )
            self.quantity += signed_delta
            self.tranches = target
        else:
            reduce_count = abs(current) - abs(target)
            delta_quantity = abs(self.quantity) * reduce_count / abs(current)
            side = "sell" if current > 0 else "buy"
            reduce_only = True
            top = snapshot.bid if side == "sell" else snapshot.ask
            price = top * (1 - self.slippage_rate if side == "sell" else 1 + self.slippage_rate)
            if current > 0:
                self.realized_pnl += delta_quantity * (price - self.entry_price)
                self.quantity -= delta_quantity
            else:
                self.realized_pnl += delta_quantity * (self.entry_price - price)
                self.quantity += delta_quantity
            self.tranches = target
            if target == 0:
                self.quantity = 0.0
                self.entry_price = None

        notional = delta_quantity * price
        fee = notional * self.taker_fee_rate
        self.fees += fee
        reference = snapshot.ask if side == "buy" else snapshot.bid
        slippage = abs(price - reference) * delta_quantity
        fill_id = hashlib.sha256(f"fill:{gate.gate_id}".encode()).hexdigest()[:24]
        fill = PaperFill(
            fill_id=fill_id,
            order_id=hashlib.sha256(f"order:{gate.gate_id}".encode()).hexdigest()[:24],
            gate_id=gate.gate_id,
            side=side,
            quantity=delta_quantity,
            price=price,
            notional=notional,
            fee=fee,
            slippage=slippage,
            reduce_only=reduce_only,
            filled_at=now,
        )
        self._fills[gate.gate_id] = fill
        return fill

    def apply_funding(self, amount: float) -> None:
        self.funding += float(amount)
