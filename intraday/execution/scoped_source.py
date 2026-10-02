"""Read-only, symbol/scope-specific execution evidence. No source initialization."""

import hashlib
import json
from datetime import datetime

from intraday.assets import ticker_symbol
from intraday.contracts import DecisionScope, FeatureSnapshot, JevDecision, ScopedRuleCandidate
from intraday.execution.source import EvidenceSource
from intraday.providers import JevDecisionProvider
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation
from intraday.spot_signal import evaluate_donchian


class ScopedEvidenceSource(EvidenceSource):
    def __init__(self, path, *, symbol, market):
        super().__init__(path)
        if market not in {"spot", "perp"}:
            raise ValueError("invalid execution market")
        self.symbol, self.market = ticker_symbol(symbol), market
        self.scope = DecisionScope.SPOT_4H if market == "spot" else DecisionScope.PERP_INTRADAY

    def route(self):
        with self.connect() as c:
            route = c.execute("SELECT * FROM asset_venue_routes WHERE symbol=? AND market=?", (self.symbol,self.market)).fetchone()
            asset = c.execute("SELECT payload_json FROM asset_catalog WHERE symbol=?", (self.symbol,)).fetchone()
        if (not asset or self.scope.value not in json.loads(asset[0])["enabled_scopes"] or not route
                or route["venue"] != "bnb" or route["environment"] != "demo" or route["instrument"] != self.symbol):
            raise ValueError("select a supported Binance Demo route for this symbol and market first")
        return dict(route)

    def rule(self):
        self.route()
        with self.connect() as c:
            row = c.execute("SELECT r.payload_json FROM scoped_rules r JOIN asset_scoped_rule_registry g ON g.champion_id=r.id "
                            "WHERE g.symbol=? AND g.scope=? AND r.status='champion'", (self.symbol,self.scope.value)).fetchone()
        if not row:
            raise ValueError("no active champion for this symbol and scope")
        rule = ScopedRuleCandidate.model_validate_json(row[0])
        if rule.symbol != self.symbol or rule.scope != self.scope:
            raise ValueError("champion identity mismatch")
        return rule

    def evaluation(self, evaluation_id, *, now):
        rule = self.rule()
        from intraday.replay_v2.compatibility import execution_gate_evaluation
        gate = execution_gate_evaluation(self.path,rule,evaluation_id,now=now)
        if gate is not None:
            return gate
        with self.connect() as c:
            row = c.execute("SELECT * FROM scoped_rule_evaluations WHERE id=?", (evaluation_id,)).fetchone()
            latest = c.execute("SELECT id,status FROM scoped_rule_evaluations WHERE candidate_id=? AND kind='soak' "
                               "ORDER BY evaluated_at DESC,id DESC LIMIT 1", (rule.rule_id,)).fetchone()
            replay = c.execute("SELECT payload_json,status FROM scoped_rule_evaluations WHERE candidate_id=? AND kind='replay' "
                               "ORDER BY evaluated_at DESC,id DESC LIMIT 1", (rule.rule_id,)).fetchone()
        if not row and self.symbol == "BTCUSDT" and self.market == "perp":
            return super().evaluation(evaluation_id, now=now)
        evaluation = ScopedRuleEvaluation.model_validate_json(row["payload_json"]) if row else None
        replay_eval = ScopedRuleEvaluation.model_validate_json(replay[0]) if replay else None
        if (not evaluation or not latest or latest["id"] != evaluation_id or latest["status"] != "pass"
                or row["status"] != "pass" or evaluation.status != "pass" or evaluation.kind != "soak"
                or evaluation.candidate_id != rule.rule_id or evaluation.symbol != self.symbol
                or evaluation.scope != self.scope or evaluation.evaluated_at > now
                or not replay_eval or replay["status"] != "pass" or replay_eval.status != "pass"
                or replay_eval.candidate_id != rule.rule_id or replay_eval.kind != "replay"
                or replay_eval.symbol != self.symbol or replay_eval.scope != self.scope
                or replay_eval.evaluated_at > evaluation.evaluated_at):
            raise ValueError("activation requires exact passing replay and latest soak for this symbol and scope")
        return evaluation

    def latest_decision(self, *, now):
        market, version = ("binance_spot", "3") if self.market == "spot" else ("binance_usdm_perp", "2")
        with self.connect() as c:
            row = c.execute("SELECT * FROM signals WHERE symbol=? AND scope=? AND market=? AND feature_schema_version=? "
                            "AND state_variant='numeric_v1' AND decision_mode='primary' ORDER BY timestamp DESC,id DESC LIMIT 1",
                            (self.symbol,self.scope.value,market,version)).fetchone()
            if not row:
                raise ValueError("no primary model-backed signal for this scope")
            at = datetime.fromisoformat(row["timestamp"])
            max_age = 14400 if self.market == "spot" else 45
            if not -2 <= (now-at).total_seconds() <= max_age:
                raise ValueError("primary scoped signal is stale")
            state = json.loads(row["state_snapshot"])
            saved = c.execute("SELECT payload_json FROM snapshots WHERE id=?", (state["snapshot_id"],)).fetchone()
            assignment = c.execute("SELECT profile_id,profile_fingerprint FROM provider_assignments WHERE role='jev'").fetchone()
            if not saved or not assignment:
                raise ValueError("missing primary signal provenance")
            snapshot = FeatureSnapshot.model_validate_json(saved[0])
            tick = f"{self.symbol}:{self.scope.value}:{int(at.timestamp()*1000)}"
            model_ref = f"{assignment[0]}@{assignment[1][:12]}"
            decision_id = hashlib.sha256(f"{model_ref}:{tick}:{snapshot.checksum}:numeric_v1:primary:{row['experiment_pair_id'] or '-'}".encode()).hexdigest()[:24]
            workflow = "spot_4h_entry" if self.market == "spot" else "perp_intraday_entry"
            call_id = hashlib.sha256(f"{workflow}:{tick}:{assignment[1]}:success:None".encode()).hexdigest()[:32]
            call = c.execute("SELECT status FROM model_calls WHERE id=?", (call_id,)).fetchone()
        if (row["decision_id"] != decision_id or not call or call[0] != "success"
                or snapshot.symbol != self.symbol or snapshot.market != market or snapshot.feature_schema_version != version
                or state != json.loads(json.dumps(JevDecisionProvider._state(snapshot,self.scope)))):
            raise ValueError("primary scoped signal provenance mismatch")
        required = (("candles_4h", "candles_8h", "candles_1d", "order_book") if self.market == "spot"
                    else ("candles", "order_book", "premium", "open_interest", "long_short_ratio"))
        if (not -2 <= (now-snapshot.built_at).total_seconds() <= max_age
                or not all(snapshot.freshness.get(name) is True for name in required)
                or any(f"{name}_missing" in snapshot.quality_flags for name in required)):
            raise ValueError("primary scoped features are stale or incomplete")
        parsed = JevDecisionProvider._parse_answers({"answers":json.loads(row["jev_answers"])})
        decision = JevDecision(decision_id=decision_id,tick_id=tick,snapshot_id=snapshot.snapshot_id,
                               direction=parsed[0],direction_confidence=parsed[1],regime=parsed[2],
                               toxic_flow=parsed[3],entry_quality=parsed[4],risk_level=parsed[5],model_ref=model_ref,created_at=at)
        return snapshot,decision,row["rules_version"]

    def spot_setup(self, *, now, rule):
        if self.market != "spot":
            raise ValueError("Spot setup requires Spot scope")
        with self.connect() as c:
            rows = c.execute("SELECT payload_json FROM asset_daily_candles WHERE symbol=? AND interval='4h' "
                             "AND close_time<? ORDER BY open_time DESC LIMIT 200", (self.symbol,int(now.timestamp()*1000))).fetchall()
        candles = [json.loads(row[0]) for row in reversed(rows)]
        if not candles or not 0 <= now.timestamp()*1000-int(candles[-1][6]) <= 14700000:
            raise ValueError("Spot closed 4h bars are stale")
        if any(int(b[0])-int(a[0]) != 14400000 for a,b in zip(candles,candles[1:])):
            raise ValueError("Spot closed-bar history has gaps")
        return evaluate_donchian(candles,rule.parameters), int(candles[-1][6])
