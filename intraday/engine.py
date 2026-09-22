"""One auditable intraday decision tick."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from intraday.contracts import (
    Direction,
    FeatureSnapshot,
    GateDecision,
    JevDecision,
    PaperFill,
    PositionSnapshot,
    Regime,
    RiskLevel,
    RiskState,
)
from intraday.cross_venue import CrossVenuePolicy
from intraday.paper import PaperPortfolio
from intraday.providers import DecisionProvider
from intraday.risk import HardRiskPolicy, RiskGate
from intraday.store import IntradayStore


@dataclass(frozen=True)
class TickResult:
    decision: JevDecision
    gate: GateDecision
    fill: PaperFill | None
    position: PositionSnapshot


class IntradayEngine:
    def __init__(
        self,
        store: IntradayStore,
        provider: DecisionProvider,
        *,
        initial_equity: float,
        policy: HardRiskPolicy | None = None,
        cross_venue_mode: str = "off",
        cross_venue_policy: CrossVenuePolicy | None = None,
    ):
        if cross_venue_mode not in {"off", "shadow", "active"}:
            raise ValueError("invalid cross-venue mode")
        self.store = store
        self.provider = provider
        self.policy = policy or HardRiskPolicy()
        self.cross_venue_mode = cross_venue_mode
        self.cross_venue_policy = cross_venue_policy or CrossVenuePolicy()
        saved = store.load_portfolio_state()
        self.portfolio = (
            PaperPortfolio.from_state(saved, policy=self.policy)
            if saved else PaperPortfolio(initial_equity, policy=self.policy)
        )
        runtime = store.load_runtime_state() or {}
        reconstructed_equity = (
            self.portfolio.initial_equity + self.portfolio.realized_pnl
            - self.portfolio.fees - self.portfolio.funding
        )
        self.high_water_mark = max(
            initial_equity,
            reconstructed_equity,
            float(runtime.get("high_water_mark", 0)),
        )
        self.news_pause_until = self._parse_time(runtime.get("news_pause_until"))
        self.halted = bool(runtime.get("halted", False))
        self.halt_reason = runtime.get("halt_reason")
        self.last_entry_at = self._parse_time(runtime.get("last_entry_at"))
        self.daily_baseline_equity = float(runtime.get("daily_baseline_equity", initial_equity))
        self.daily_baseline_date = runtime.get("daily_baseline_date")
        self.notifications_enabled = bool(runtime.get("notifications_enabled", True))
        active_rule = store.load_active_rule()
        self.gate = RiskGate(
            self.policy,
            rule_id=active_rule.rule_id if active_rule else "rule-v1",
            rule=active_rule.parameters if active_rule else None,
        )

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value)

    def _runtime_state(self) -> dict:
        return {
            "high_water_mark": self.high_water_mark,
            "news_pause_until": (
                self.news_pause_until.isoformat() if self.news_pause_until else None
            ),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "last_entry_at": self.last_entry_at.isoformat() if self.last_entry_at else None,
            "daily_baseline_equity": self.daily_baseline_equity,
            "daily_baseline_date": self.daily_baseline_date,
            "notifications_enabled": self.notifications_enabled,
        }

    def reload_active_rule(self) -> None:
        active_rule = self.store.load_active_rule()
        self.gate = RiskGate(
            self.policy,
            rule_id=active_rule.rule_id if active_rule else "rule-v1",
            rule=active_rule.parameters if active_rule else None,
        )

    def pause_entries(self, reason: str, *, now: datetime) -> None:
        self.halted = True
        self.halt_reason = reason
        self.store.save_runtime_state(self._runtime_state(), updated_at=now)

    def resume_entries(self, *, now: datetime) -> None:
        self.halted = False
        self.halt_reason = None
        self.store.save_runtime_state(self._runtime_state(), updated_at=now)

    def set_news_pause(self, pause_until: datetime | None, *, now: datetime) -> None:
        self.news_pause_until = pause_until
        self.store.save_runtime_state(self._runtime_state(), updated_at=now)

    def manual_flatten(
        self,
        snapshot: FeatureSnapshot,
        *,
        command_id: str,
        now: datetime,
    ) -> PaperFill | None:
        """Create an audited reduce-only transition and keep entries paused afterward."""
        decision_id = hashlib.sha256(f"manual:{command_id}".encode()).hexdigest()[:24]
        decision = JevDecision(
            decision_id=decision_id,
            tick_id=f"command:{command_id}",
            snapshot_id=snapshot.snapshot_id,
            direction=Direction.HOLD,
            direction_confidence=1,
            regime=Regime.VOLATILE,
            toxic_flow=0,
            entry_quality=1,
            risk_level=RiskLevel.HIGH,
            model_ref="operator/manual-v1",
            created_at=now,
            latency_ms=0,
        )
        gate = GateDecision(
            gate_id=hashlib.sha256(f"manual-gate:{command_id}".encode()).hexdigest()[:24],
            decision_id=decision_id,
            rule_id=self.gate.rule_id,
            outcome="reduce_only" if self.portfolio.tranches else "hold",
            target_tranches=0,
            authorized_notional=0,
            reason_codes=("operator_flatten",),
            evaluated_at=now,
        )
        fill = self.portfolio.apply(gate, snapshot, now=now)
        self.halted = True
        self.halt_reason = "operator_flatten"
        self.store.record_tick(
            snapshot,
            decision,
            gate,
            fill,
            portfolio_state=self.portfolio.export_state(),
            runtime_state=self._runtime_state(),
        )
        return fill

    @staticmethod
    def tick_id(snapshot: FeatureSnapshot) -> str:
        bucket = int(snapshot.event_time.timestamp()) // 5
        return f"{snapshot.symbol}:{bucket}"

    @staticmethod
    def _fallback(snapshot: FeatureSnapshot, tick_id: str, now: datetime) -> JevDecision:
        decision_id = hashlib.sha256(f"fallback:{tick_id}:{snapshot.checksum}".encode()).hexdigest()[:24]
        return JevDecision(
            decision_id=decision_id,
            tick_id=tick_id,
            snapshot_id=snapshot.snapshot_id,
            direction=Direction.HOLD,
            direction_confidence=0,
            regime=Regime.VOLATILE,
            toxic_flow=1,
            entry_quality=1,
            risk_level=RiskLevel.CRITICAL,
            model_ref="fallback/hold-v1",
            created_at=now,
        )

    def _risk_state(self, mark_price: float, now: datetime) -> RiskState:
        equity = self.portfolio.equity(mark_price)
        self.high_water_mark = max(self.high_water_mark, equity)
        drawdown = equity / self.high_water_mark - 1
        current_date = now.date().isoformat()
        if self.daily_baseline_date != current_date:
            self.daily_baseline_date = current_date
            self.daily_baseline_equity = equity
        daily_return = equity / self.daily_baseline_equity - 1
        return RiskState(
            equity=equity,
            high_water_mark=self.high_water_mark,
            daily_return=daily_return,
            drawdown=drawdown,
            halted=self.halted,
            halt_reason=self.halt_reason,
            news_pause_until=self.news_pause_until,
            last_entry_at=self.last_entry_at,
        )

    def run_tick(self, snapshot: FeatureSnapshot, *, now: datetime | None = None) -> TickResult:
        now = now or datetime.now(timezone.utc)
        tick_id = self.tick_id(snapshot)
        existing = self.store.get_tick(tick_id)
        mark_price = float(snapshot.features.get("mark_price") or snapshot.features["price"])
        if existing is not None:
            return TickResult(
                decision=existing["decision"],
                gate=existing["gate"],
                fill=existing["fill"],
                position=self.portfolio.position(mark_price),
            )
        try:
            decision = self.provider.decide(snapshot, tick_id, now)
        except Exception:
            decision = self._fallback(snapshot, tick_id, now)
        cross_venue = (
            self.cross_venue_policy.assess(snapshot, decision)
            if self.cross_venue_mode != "off" else None
        )
        before = self.portfolio.tranches
        gate = self.gate.evaluate(
            snapshot,
            decision,
            self._risk_state(mark_price, now),
            self.portfolio.position(mark_price),
            now,
            cross_venue=cross_venue,
            cross_venue_mode=self.cross_venue_mode,
        )
        fill = self.portfolio.apply(gate, snapshot, now=now)
        if fill is not None and abs(self.portfolio.tranches) > abs(before):
            self.last_entry_at = now
        self.store.record_tick(
            snapshot,
            decision,
            gate,
            fill,
            portfolio_state=self.portfolio.export_state(),
            runtime_state=self._runtime_state(),
        )
        return TickResult(
            decision=decision,
            gate=gate,
            fill=fill,
            position=self.portfolio.position(mark_price),
        )
