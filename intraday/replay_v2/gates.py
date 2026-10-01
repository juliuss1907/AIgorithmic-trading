"""Pure, versioned admission policy; never changes a rule or sends an order."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from intraday.replay_v2.contracts import FrozenModel, ReplayConfig, ExecutionProfile, binance_gate_profile
from intraday.replay_v2.metrics import fingerprint


GATE_VERSION = "gate-v2.1"


def profile_fingerprint(profile: ExecutionProfile):
    # Funding and instrument snapshots are separately bound through dataset/config
    # hashes; economic assumptions remain fixed across historical/post-gate runs.
    return fingerprint(profile.model_dump(mode="json", exclude={"funding", "instrument",
                       "instrument_observed_at", "instrument_source"}))


class GateEvidence(FrozenModel):
    history_bars: int = Field(default=0, ge=0)
    history_coverage: float = Field(default=0, ge=0, le=1)
    latest_candle_age_seconds: float | None = Field(default=None, ge=0)
    elapsed_days: float = Field(default=0, ge=0)
    outcome_count: int = Field(default=0, ge=0)
    outcome_coverage: float = Field(default=0, ge=0, le=1)
    heartbeat_coverage: float = Field(default=0, ge=0, le=1)
    quote_coverage: float = Field(default=0, ge=0, le=1)
    verified_decisions: int = Field(default=0, ge=0)
    hard_risk_violations: int = Field(default=0, ge=0)


class GateOutcome(FrozenModel):
    status: Literal["pass", "reject", "deferred"]
    reason_codes: tuple[str, ...]
    metrics: dict[str, float | int | None]


class GateEvaluation(FrozenModel):
    evaluation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    engine_version: Literal["gate-v2.1"] = GATE_VERSION
    candidate_id: str
    rule_content_hash: str
    kind: Literal["replay", "soak"]
    status: Literal["pass", "reject", "deferred"]
    evaluated_at: datetime
    campaign_id: str | None = None
    replay_evaluation_id: str | None = None
    replay_config: ReplayConfig
    profile_hash: str
    run_id: str | None = None
    result_id: str | None = None
    dataset_checksum: str | None = None
    funding_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    metrics: dict[str, float | int | None] = Field(default_factory=dict)

    @property
    def symbol(self):
        return self.replay_config.symbol

    @property
    def scope(self):
        return self.replay_config.scope

    @classmethod
    def create(cls, **values):
        payload = cls.model_construct(evaluation_id="0"*32, **values).model_dump(mode="json", exclude={"evaluation_id"})
        return cls(evaluation_id=fingerprint(payload)[:32], **values)


def _audit_entries(report):
    violations = 0
    config = ReplayConfig.model_validate(report["config"])
    for event in report.get("events", ()):
        if event["kind"] != "entry":
            continue
        audit = event.get("risk_audit")
        if audit is None:
            raise ValueError("gate replay requires fill risk audit evidence")
        equity, notional = Decimal(audit["pre_fill_equity"]), Decimal(audit["notional"])
        cap = Decimal(".30") if config.market == "spot" else Decimal(".20")
        safe = 0 < notional <= min(config.capital, equity)*cap
        if config.market == "perp":
            safe = safe and notional/config.leverage <= equity*Decimal(".10")
        safe = safe and not audit["entries_paused"] and (config.market != "spot" or event["side"] == 1)
        violations += not safe
    return violations


def evaluate_report_gate(report, evidence: GateEvidence, *, champion=None, kind="replay"):
    config = ReplayConfig.model_validate(report["config"])
    if profile_fingerprint(config.profile) != profile_fingerprint(binance_gate_profile()):
        raise ValueError("gate requires the approved Binance regular-user profile")
    if report["evaluator_version"] != "replay-v2.2":
        raise ValueError("gate requires replay-v2.2 accounting")
    summary = report["summary"]
    waiting, rejected = [], []
    metrics = {**evidence.model_dump(), **{key:summary.get(key) for key in (
        "net_return_pct", "max_drawdown_known_pct", "closed_trades",
        "daily_expected_shortfall_known_pct", "exchange_fee_known", "slippage_cost_known", "funding_paid_known")}}
    violations = evidence.hard_risk_violations+_audit_entries(report)
    metrics["hard_risk_violations"] = violations
    if violations:
        rejected.append("hard_risk_violation")
    if config.market == "spot":
        if evidence.history_bars < 2190:
            waiting.append("minimum_365_day_history")
        if evidence.history_coverage < .99:
            waiting.append("4h_candle_coverage_below_99pct")
        if evidence.latest_candle_age_seconds is None or evidence.latest_candle_age_seconds > 14700:
            waiting.append("latest_4h_candle_stale")
    else:
        if evidence.elapsed_days < 14:
            waiting.append("minimum_14_days" if kind == "replay" else "minimum_14_days_post_gate")
        if evidence.outcome_count < 100:
            waiting.append("minimum_100_matured_outcomes")
        for name in ("outcome_coverage", "heartbeat_coverage", "quote_coverage"):
            if getattr(evidence, name) < .95:
                waiting.append(name+"_below_95pct")
        if evidence.verified_decisions < 100:
            waiting.append("minimum_100_verified_decisions")
        if not summary.get("funding_complete") or summary.get("net_return_pct") is None:
            waiting.append("funding_coverage_incomplete")
        if any(code.startswith("unverified_recorded_decisions:") for code in report["limitations"]):
            waiting.append("recorded_provenance_incomplete")
    if report["status"] == "insufficient_data" or any(code in report["limitations"] for code in (
        "spot_warmup_or_history_gap", "price_history_starts_after_window", "price_history_ends_before_window",
        "no_valid_perp_quotes", "no_verified_recorded_decisions")):
        waiting.append("replay_price_or_decision_history_incomplete")
    if summary.get("closed_trades", 0) < 6:
        waiting.append("minimum_6_closed_trades")
    net, drawdown = summary.get("net_return_pct"), summary.get("max_drawdown_known_pct")
    if net is not None and net <= 0:
        rejected.append("nonpositive_net_return")
    if drawdown is None:
        waiting.append("drawdown_unknown")
    elif drawdown >= 8:
        rejected.append("max_drawdown_at_least_8pct")
    metrics["risk_score"] = net-drawdown if net is not None and drawdown is not None else None
    if champion:
        other = ReplayConfig.model_validate(champion["config"])
        if config.model_dump(exclude={"rule_id"}) != other.model_dump(exclude={"rule_id"}):
            raise ValueError("champion comparison requires same window, capital, leverage and profile")
        base = champion["summary"]
        if base["net_return_pct"] is None or base["daily_expected_shortfall_known_pct"] is None:
            waiting.append("champion_metrics_incomplete")
        elif metrics["risk_score"] is not None:
            score = base["net_return_pct"]-base["max_drawdown_known_pct"]
            metrics["champion_risk_score"] = score
            if metrics["risk_score"] < (score*.9 if score > 0 else score):
                rejected.append("risk_score_below_90pct_champion")
            tail = summary.get("daily_expected_shortfall_known_pct")
            if tail is None:
                waiting.append("expected_shortfall_unknown")
            elif tail < base["daily_expected_shortfall_known_pct"]-.25:
                rejected.append("expected_shortfall_worse_than_champion")
    status = "reject" if violations else "deferred" if waiting else "reject" if rejected else "pass"
    return GateOutcome(status=status, reason_codes=tuple(dict.fromkeys(waiting+rejected)), metrics=metrics)
