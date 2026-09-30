"""Read-only bridge from existing BTC soak evidence; never initialize IntradayStore."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from intraday.contracts import DecisionScope, FeatureSnapshot, JevDecision, ScopedRuleCandidate
from intraday.portfolio_soak import CURRENT_SOAK_EVIDENCE_VERSION, PortfolioSoakEvaluation
from intraday.providers import JevDecisionProvider


class EvidenceSource:
    def __init__(self, path: Path):
        self.path = Path(path).resolve(strict=True)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            yield connection
        finally:
            connection.close()

    def evaluation(self, evaluation_id: str, *, now: datetime):
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM portfolio_soak_evaluations WHERE id=?", (evaluation_id,)).fetchone()
            latest = connection.execute("SELECT status FROM portfolio_soak_evaluations ORDER BY evaluated_at DESC, id DESC LIMIT 1").fetchone()
        evaluation = PortfolioSoakEvaluation.model_validate_json(row[0]) if row else None
        if (evaluation is None or evaluation.status != "pass" or not latest or latest[0] != "pass"
                or evaluation.evidence_version != CURRENT_SOAK_EVIDENCE_VERSION
                or evaluation.hard_risk_violations or evaluation.evaluated_at > now):
            raise ValueError("Demo activation requires current passing BTC portfolio soak evidence")
        return evaluation

    def rule(self):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT r.payload_json FROM scoped_rules r JOIN asset_scoped_rule_registry g ON g.champion_id=r.id "
                "WHERE g.symbol='BTCUSDT' AND g.scope='perp_intraday' AND r.status='champion'"
            ).fetchone()
        if row is None:
            raise ValueError("Demo requires an active BTC Perp champion")
        rule = ScopedRuleCandidate.model_validate_json(row[0])
        if rule.symbol != "BTCUSDT" or rule.scope != DecisionScope.PERP_INTRADAY:
            raise ValueError("invalid BTC Perp champion")
        return rule

    def latest_decision(self, *, now: datetime):
        """Verify the original model call and checksum, not just the displayed signal label."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM signals WHERE symbol='BTCUSDT' AND scope='perp_intraday' "
                "AND market='binance_usdm_perp' AND feature_schema_version='2' "
                "AND state_variant='numeric_v1' AND decision_mode='primary' "
                "ORDER BY timestamp DESC, id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise ValueError("no primary BTC Perp signal")
            at = datetime.fromisoformat(row["timestamp"])
            if not -2 <= (now - at).total_seconds() <= 45:
                raise ValueError("BTC Perp signal is stale")
            state = json.loads(row["state_snapshot"])
            saved = connection.execute("SELECT payload_json FROM snapshots WHERE id=?", (state["snapshot_id"],)).fetchone()
            assignment = connection.execute("SELECT profile_id, profile_fingerprint FROM provider_assignments WHERE role='jev'").fetchone()
            if saved is None or assignment is None:
                raise ValueError("missing model-backed signal provenance")
            snapshot = FeatureSnapshot.model_validate_json(saved[0])
            tick_id = f"BTCUSDT:perp_intraday:{int(at.timestamp() * 1000)}"
            model_ref = f"{assignment[0]}@{assignment[1][:12]}"
            decision_id = hashlib.sha256(
                f"{model_ref}:{tick_id}:{snapshot.checksum}:numeric_v1:primary:{row['experiment_pair_id'] or '-'}".encode()
            ).hexdigest()[:24]
            call_id = hashlib.sha256(f"perp_intraday_entry:{tick_id}:{assignment[1]}:success:None".encode()).hexdigest()[:32]
            call = connection.execute("SELECT status FROM model_calls WHERE id=?", (call_id,)).fetchone()
        if row["decision_id"] != decision_id or not call or call[0] != "success":
            raise ValueError("signal is not a verified primary model decision")
        if (snapshot.symbol != "BTCUSDT" or snapshot.market != "binance_usdm_perp"
                or snapshot.feature_schema_version != "2"
                or state != json.loads(json.dumps(JevDecisionProvider._state(snapshot, DecisionScope.PERP_INTRADAY)))):
            raise ValueError("signal snapshot provenance mismatch")
        required = ("candles", "order_book", "premium", "open_interest", "long_short_ratio")
        if (not -2 <= (now - snapshot.built_at).total_seconds() <= 45
                or not all(snapshot.freshness.get(name) is True for name in required)
                or any(f"{name}_missing" in snapshot.quality_flags for name in required)):
            raise ValueError("stale primary market features")
        parsed = JevDecisionProvider._parse_answers({"answers": json.loads(row["jev_answers"])})
        decision = JevDecision(
            decision_id=decision_id, tick_id=tick_id, snapshot_id=snapshot.snapshot_id,
            direction=parsed[0], direction_confidence=parsed[1], regime=parsed[2],
            toxic_flow=parsed[3], entry_quality=parsed[4], risk_level=parsed[5],
            model_ref=model_ref, created_at=at,
        )
        return snapshot, decision, row["rules_version"]
