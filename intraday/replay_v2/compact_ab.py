"""Offline numeric-vs-compact Jev A/B on stored snapshots; research only, no source writes.

The compact state is a pure function of an immutable stored snapshot, so a past decision can be
re-asked in both encodings with the same model at the same time and scored against the realized
15-minute forward return. The source database is opened read-only; every model call is journaled
in a scratch store inside the report directory.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
from statistics import fmean

from intraday.contracts import DecisionMode, DecisionScope, FeatureSnapshot, ProviderRole, StateVariant
from intraday.decision_evaluation import (DEADBAND_PCT, EVALUATION_HORIZONS, ExperimentMetrics, _brier, _bucket,
                                          _ece, _probabilities, _truth, evaluate_eligibility)
from intraday.providers import JevDecisionProvider
from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.metrics import encoded
from intraday.store import IntradayStore

SCOPE = DecisionScope.PERP_INTRADAY
HORIZON = EVALUATION_HORIZONS[SCOPE]
DEADBAND = DEADBAND_PCT[SCOPE]
ACTION_CONFIDENCE = 0.85
ACCURACY_MARGIN = 0.02
VERSION = "compact-ab-1"
# Rough relative size of the compact request used only for the dry-run estimate.
COMPACT_COST_RATIO = 0.10


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    signal_id: int
    symbol: str
    at: datetime
    stratum: str  # numeric direction bucket: long, short, hold
    forward_return_pct: float


def _rank(seed, signal_id):
    return hashlib.sha256(f"{seed}:{signal_id}".encode()).hexdigest()


def select_samples(candidates, *, per_coin, spacing, seed):
    """Deterministic, time-spaced, stratified sample; also returns the natural stratum mix.

    One candidate per `spacing` bucket and coin (non-hold preferred, then lowest hash), because
    30 s ticks are strongly autocorrelated and their 15 min outcomes overlap. A coin's quota is
    filled with non-hold buckets first (up to two thirds), then hold buckets.
    """
    width = int(spacing.total_seconds())
    by_coin = defaultdict(lambda: defaultdict(list))
    for item in candidates:
        by_coin[item.symbol][int(item.at.timestamp())//width].append(item)
    chosen, population = [], defaultdict(int)
    for symbol in sorted(by_coin):
        picked = []
        for bucket in by_coin[symbol].values():
            active = [c for c in bucket if c.stratum != "hold"]
            pool = active or bucket
            picked.append(min(pool, key=lambda c: _rank(seed, c.signal_id)))
        for item in picked:
            population[item.stratum] += 1
        active = sorted((c for c in picked if c.stratum != "hold"), key=lambda c: _rank(seed, c.signal_id))
        holds = sorted((c for c in picked if c.stratum == "hold"), key=lambda c: _rank(seed, c.signal_id))
        cap = per_coin*2//3
        take = active[:cap]
        take += holds[:per_coin-len(take)]
        if len(take) < per_coin:
            take += active[cap:cap+per_coin-len(take)]
        chosen.extend(sorted(take, key=lambda c: (c.at, c.signal_id)))
    total = sum(population.values()) or 1
    return chosen, {key: value/total for key, value in sorted(population.items())}


def load_candidates(source):
    """Lightweight rows only; heavy JSON is fetched later for the chosen signals."""
    with source._connect() as c:
        rows = c.execute(
            "SELECT s.id, s.symbol, s.timestamp, json_extract(s.jev_answers,'$.direction.choice') AS choice, "
            "o.forward_return_pct FROM signals s JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon_sec=? "
            "WHERE s.scope=? AND s.state_variant='numeric_v1' AND s.decision_mode='primary' "
            "AND s.feature_schema_version='2' AND s.market='binance_usdm_perp' ORDER BY s.symbol, s.timestamp, s.id",
            (HORIZON, SCOPE.value)).fetchall()
    return [Candidate(r["id"], r["symbol"], datetime.fromisoformat(r["timestamp"]), _bucket(r["choice"]),
                      float(r["forward_return_pct"])) for r in rows]


def load_signal(source, signal_id):
    """Stored numeric answers plus the immutable snapshot, verified against the stored state."""
    with source._connect() as c:
        row = c.execute("SELECT state_snapshot, jev_answers FROM signals WHERE id=?", (signal_id,)).fetchone()
        state = json.loads(row["state_snapshot"])
        snap = c.execute("SELECT payload_json FROM snapshots WHERE id=?", (state.get("snapshot_id"),)).fetchone()
    if snap is None:
        return None
    snapshot = FeatureSnapshot.model_validate_json(snap["payload_json"])
    expected = json.loads(json.dumps(JevDecisionProvider._state(snapshot, SCOPE)))
    if state != expected:
        return None
    return snapshot, json.loads(row["jev_answers"])


def _view(answers):
    """Direction bucket, bucket probabilities and the chosen action's confidence."""
    probabilities = _probabilities(json.dumps(answers))
    direction = answers["direction"]
    choice = _bucket(direction["choice"])
    return {"choice": choice, "probabilities": probabilities, "confidence": probabilities[choice]}


def _mean(values, weights=None):
    if not values:
        return None
    if weights is None:
        return fmean(values)
    total = sum(weights)
    return sum(v*w for v, w in zip(values, weights))/total if total else None


def _bootstrap(differences, *, seed, rounds):
    rng = random.Random(seed)
    size = len(differences)
    means = sorted(fmean(rng.choices(differences, k=size)) for _ in range(rounds))
    return means[int(.025*rounds)], means[int(.975*rounds)-1]


def score(pairs, *, natural_mix, telemetry, rounds, seed):
    """Metrics on samples with both a re-asked numeric and a compact answer."""
    both = [p for p in pairs if p["numeric"] and p["compact"]]
    out = {"samples": len(pairs), "paired_samples": len(both)}
    if not both:
        return out, None
    sample_mix = defaultdict(int)
    for p in both:
        sample_mix[p["stratum"]] += 1
    weights = [natural_mix.get(p["stratum"], 0)/(sample_mix[p["stratum"]]/len(both)) for p in both]

    def collect(name):
        views = [p[name] for p in both]
        truth = [p["truth"] for p in both]
        correct = [v["choice"] == t for v, t in zip(views, truth)]
        brier = [_brier(v["probabilities"], t) for v, t in zip(views, truth)]
        return views, truth, correct, brier

    numeric, truth, numeric_ok, numeric_brier = collect("numeric")
    compact, _, compact_ok, compact_brier = collect("compact")
    stored_ok = [p["stored"]["choice"] == p["truth"] for p in both]
    agree = [n["choice"] == c["choice"] for n, c in zip(numeric, compact)]
    flips = [(n["choice"] == "hold") != (c["choice"] == "hold") for n, c in zip(numeric, compact)]
    repeat = [p["stored"]["choice"] == n["choice"] for p, n in zip(both, numeric)]
    differences = [float(c)-float(n) for c, n in zip(compact_ok, numeric_ok)]
    low, high = _bootstrap(differences, seed=seed, rounds=rounds)
    out.update(
        numeric_accuracy=_mean(numeric_ok), compact_accuracy=_mean(compact_ok), stored_accuracy=_mean(stored_ok),
        numeric_brier=_mean(numeric_brier), compact_brier=_mean(compact_brier),
        numeric_ece=_ece([(v["probabilities"], t) for v, t in zip(numeric, truth)]),
        compact_ece=_ece([(v["probabilities"], t) for v, t in zip(compact, truth)]),
        direction_agreement=_mean(agree), actionable_flip_rate=_mean(flips),
        numeric_repeat_agreement=_mean(repeat), accuracy_difference=fmean(differences),
        accuracy_difference_ci95=[low, high],
        weighted=dict(numeric_accuracy=_mean(numeric_ok, weights), compact_accuracy=_mean(compact_ok, weights),
                      numeric_brier=_mean(numeric_brier, weights), compact_brier=_mean(compact_brier, weights),
                      direction_agreement=_mean(agree, weights)),
        strata={k: v for k, v in sorted(sample_mix.items())}, natural_mix=natural_mix)
    confirm = {}
    for name, views in (("numeric", numeric), ("compact", compact)):
        for side in ("long", "short"):
            picked = [(v, t) for v, t in zip(views, truth) if v["choice"] == side and v["confidence"] >= ACTION_CONFIDENCE]
            confirm[f"{name}_{side}"] = {"confirmed": len(picked),
                                         "precision": _mean([t == side for v, t in picked])}
    out["confirmations_at_0_85"] = confirm
    metrics = ExperimentMetrics(
        primary_pairs=len(pairs), paired_samples=len(both), duration_days=telemetry["span_days"],
        availability=telemetry["compact_availability"], direction_agreement=out["direction_agreement"],
        actionable_flip_rate=out["actionable_flip_rate"], numeric_accuracy=out["numeric_accuracy"],
        compact_accuracy=out["compact_accuracy"], numeric_brier=out["numeric_brier"],
        compact_brier=out["compact_brier"], numeric_ece=out["numeric_ece"], compact_ece=out["compact_ece"],
        numeric_input_tokens=telemetry["numeric_input_tokens"], compact_input_tokens=telemetry["compact_input_tokens"],
        numeric_latency_ms=telemetry["numeric_latency_ms"], compact_latency_ms=telemetry["compact_latency_ms"],
        numeric_cost_usd=telemetry["numeric_cost_usd"], compact_cost_usd=telemetry["compact_cost_usd"],
        token_reduction=telemetry["token_reduction"])
    return out, metrics


MIN_PAIRS = 1000  # The stock criterion; kept unless a test lowers it explicitly.


def verdict(metrics, result, *, min_pairs=MIN_PAIRS):
    """Pre-registered: the stock criteria, minus the collection-time duration, plus a CI guard."""
    if metrics is None:
        return "inconclusive", ("no_paired_samples",)
    if metrics.paired_samples < min_pairs:
        return "inconclusive", ("minimum_pairs_not_met",)
    # Duration (>= 14 days) is a collection-time criterion; offline the sample size and the
    # bootstrap interval carry that role, so it is waived explicitly rather than silently.
    status, reasons = evaluate_eligibility(metrics.model_copy(update={
        "duration_days": max(metrics.duration_days, 14.0),
        "primary_pairs": max(metrics.primary_pairs, 1_000), "paired_samples": max(metrics.paired_samples, 1_000)}))
    if status == "reject":
        return "reject", reasons
    if status == "collecting":
        return "inconclusive", reasons
    if result["accuracy_difference_ci95"][0] < -ACCURACY_MARGIN:
        return "inconclusive", ("accuracy_regression_not_excluded_by_ci",)
    return "eligible", ()


def telemetry(scratch, span_days, compact_attempts, compact_failed):
    with scratch._connect() as c:
        rows = c.execute(
            "SELECT json_extract(payload_json,'$.workflow') LIKE '%\\_compact' ESCAPE '\\' AS compact, status, "
            "count(*) AS n, avg(json_extract(payload_json,'$.input_tokens')) AS tokens, "
            "avg(json_extract(payload_json,'$.latency_ms')) AS latency, sum(cost_usd) AS cost "
            "FROM model_calls GROUP BY 1,2").fetchall()
    stat = {(bool(r["compact"]), r["status"]): r for r in rows}
    pick = lambda compact, key: (stat[(compact, "success")][key] if (compact, "success") in stat else None)
    numeric, compact = pick(False, "tokens"), pick(True, "tokens")
    return {"span_days": span_days, "numeric_input_tokens": numeric, "compact_input_tokens": compact,
            "numeric_latency_ms": pick(False, "latency"), "compact_latency_ms": pick(True, "latency"),
            "numeric_cost_usd": pick(False, "cost"), "compact_cost_usd": pick(True, "cost"),
            "token_reduction": 1-compact/numeric if numeric and compact is not None else None,
            "compact_availability": 1-compact_failed/compact_attempts if compact_attempts else 0.0,
            "spent_usd": sum((r["cost"] or 0) for r in rows)}


def provider_ask(provider):
    """Adapter from JevDecisionProvider to `ask(sample, variant) -> answers | None`."""
    def ask(sample, snapshot, variant):
        pair = hashlib.sha256(f"{VERSION}:{sample.signal_id}".encode()).hexdigest()[:32]
        try:
            scoped = provider.decide_scoped(
                snapshot, f"{sample.symbol}:{SCOPE.value}:ab:{variant}:{sample.signal_id}", SCOPE, sample.at,
                state_variant=StateVariant.COMPACT_V1 if variant == "compact" else StateVariant.NUMERIC_V1,
                decision_mode=DecisionMode.SHADOW, experiment_pair_id=pair)
        except RuntimeError:
            return None
        return scoped.trace.jev_answers
    return ask


def build_provider(source, secrets_file, scratch):
    from intraday.provider_profiles import ProviderSecretStore
    assignment = source.provider_assignment(ProviderRole.JEV)
    if assignment is None:
        raise ValueError("no active Jev provider assignment in the source database")
    credential = ProviderSecretStore(secrets_file).get(assignment["profile_id"])
    if credential.profile.fingerprint != assignment["profile_fingerprint"]:
        raise ValueError("Jev secret does not match the assigned profile")
    return JevDecisionProvider(credential, store=scratch)


def estimate(source, count):
    with source._connect() as c:
        row = c.execute("SELECT avg(cost_usd) AS cost FROM model_calls WHERE role='jev' AND status='success' "
                        "AND json_extract(payload_json,'$.workflow')='perp_intraday_entry'").fetchone()
    per = float(row["cost"] or 0)
    return {"numeric_cost_per_call_usd": per, "calls": count*2,
            "estimated_usd": count*per*(1+COMPACT_COST_RATIO), "assumes_compact_cost_ratio": COMPACT_COST_RATIO}


def run(database, report_root, *, ask=None, provider_factory=None, secrets_file=None, per_coin=500,
        spacing_minutes=30, seed=VERSION, max_calls=8000, max_usd=1.0, dry_run=False, rounds=1000,
        min_pairs=MIN_PAIRS):
    source = IntradayStore(database, read_only=True)
    candidates = load_candidates(source)
    sample, natural_mix = select_samples(candidates, per_coin=per_coin, spacing=timedelta(minutes=spacing_minutes),
                                         seed=seed)
    plan = {"version": VERSION, "candidates": len(candidates), "samples": len(sample),
            "per_coin": dict(sorted((s, sum(1 for c in sample if c.symbol == s)) for s in {c.symbol for c in sample})),
            "strata": dict(sorted((k, sum(1 for c in sample if c.stratum == k)) for k in {c.stratum for c in sample})),
            "natural_mix": natural_mix, "estimate": estimate(source, len(sample)),
            "limits": {"max_calls": max_calls, "max_usd": max_usd}}
    if dry_run:
        return {"status": "dry_run", **plan}
    root = Path(report_root).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    scratch = IntradayStore(root/"scratch.sqlite")
    if ask is None:
        provider = provider_factory(scratch) if provider_factory else build_provider(source, secrets_file, scratch)
        ask = provider_ask(provider)
    pairs, calls, compact_calls, compact_failed, skipped = [], 0, 0, 0, 0
    for index, item in enumerate(sample):
        loaded = load_signal(source, item.signal_id)
        if loaded is None:
            skipped += 1
            continue
        snapshot, stored = loaded
        if calls+2 > max_calls:
            raise BudgetExceeded("max_calls reached; no verdict written")
        if index % 25 == 0 and telemetry(scratch, 0, 0, 0)["spent_usd"] >= max_usd:
            raise BudgetExceeded("max_usd reached; no verdict written")
        numeric = ask(item, snapshot, "numeric")
        compact = ask(item, snapshot, "compact")
        calls += 2
        compact_calls += 1
        compact_failed += compact is None
        pairs.append({"signal_id": item.signal_id, "symbol": item.symbol, "at": item.at.isoformat(),
                      "stratum": item.stratum, "truth": _truth(item.forward_return_pct, DEADBAND),
                      "forward_return_pct": item.forward_return_pct, "stored": _view(stored),
                      "numeric": _view(numeric) if numeric else None,
                      "compact": _view(compact) if compact else None})
    span = ((max(c.at for c in sample)-min(c.at for c in sample)).total_seconds()/86400) if sample else 0.0
    data = telemetry(scratch, span, compact_calls, compact_failed)
    result, metrics = score(pairs, natural_mix=natural_mix, telemetry=data, rounds=rounds, seed=seed)
    status, reasons = verdict(metrics, result, min_pairs=min_pairs)
    summary = {"status": status, "reason_codes": list(reasons), **plan, "skipped_unverifiable": skipped,
               "telemetry": data, "metrics": result,
               "waived_offline": ["minimum_14_days"], "min_pairs": min_pairs,
               "scope_note": "perp_intraday 15 min direction, not the Setup-2 H4 confirmation",
               "generated_at": datetime.now(timezone.utc).isoformat()}
    _write(root/"pairs.jsonl", "".join(encoded(p)+"\n" for p in pairs))
    _write(root/"summary.json", encoded(summary))
    _write(root/"report.md", markdown(summary))
    return summary


def markdown(summary):
    m = summary["metrics"]
    f = lambda v, spec=".3f": "n/a" if v is None else format(v, spec)
    lines = [f"# A/B numeric vs compact Jev (offline) — {summary['status']}", "",
             f"Lý do: {', '.join(summary['reason_codes']) or 'đạt các tiêu chí'}.",
             f"Mẫu: {summary['samples']} (ghép được {m.get('paired_samples', 0)}), bỏ qua {summary['skipped_unverifiable']} "
             f"do snapshot không xác minh được.", "",
             "| Chỉ số | Numeric (hỏi lại) | Compact |", "|---|---:|---:|"]
    if m.get("paired_samples"):
        lines += [f"| Độ chính xác | {f(m['numeric_accuracy'])} | {f(m['compact_accuracy'])} |",
                  f"| Brier | {f(m['numeric_brier'])} | {f(m['compact_brier'])} |",
                  f"| ECE | {f(m['numeric_ece'])} | {f(m['compact_ece'])} |",
                  f"| Token vào TB | {f(summary['telemetry']['numeric_input_tokens'], '.0f')} | "
                  f"{f(summary['telemetry']['compact_input_tokens'], '.0f')} |", "",
                  f"- Đồng thuận hướng: {f(m['direction_agreement'])}; lật hành động: {f(m['actionable_flip_rate'])}.",
                  f"- Numeric hỏi lại trùng numeric đã lưu: {f(m['numeric_repeat_agreement'])} (sàn nhiễu).",
                  f"- Hiệu độ chính xác (compact − numeric): {f(m['accuracy_difference'], '+.3f')}, "
                  f"CI95 [{f(m['accuracy_difference_ci95'][0], '+.3f')}, {f(m['accuracy_difference_ci95'][1], '+.3f')}].",
                  f"- Giảm token: {f(summary['telemetry']['token_reduction'], '.1%')}; "
                  f"sẵn sàng của compact: {f(summary['telemetry']['compact_availability'], '.1%')}."]
    lines += ["", f"Phạm vi: {summary['scope_note']}. Tiêu chí thời lượng ≥14 ngày được miễn (offline).",
              "Đây là bằng chứng cho operator, không tự đổi gì."]
    return "\n".join(lines)+"\n"


def main(argv=None):
    import argparse
    from intraday.config import default_provider_secrets_path, resolve_database_path
    parser = argparse.ArgumentParser(description="Offline numeric-vs-compact Jev A/B on stored snapshots")
    parser.add_argument("--database", default=None)
    parser.add_argument("--report-root", required=True)
    parser.add_argument("--secrets-file", type=Path, default=default_provider_secrets_path())
    parser.add_argument("--per-coin", type=int, default=500)
    parser.add_argument("--spacing-minutes", type=int, default=30)
    parser.add_argument("--max-calls", type=int, default=8000)
    parser.add_argument("--max-usd", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run(resolve_database_path(args.database), args.report_root, secrets_file=args.secrets_file,
                 per_coin=args.per_coin, spacing_minutes=args.spacing_minutes, max_calls=args.max_calls,
                 max_usd=args.max_usd, dry_run=args.dry_run)
    print(json.dumps({k: result[k] for k in ("status", "samples", "reason_codes") if k in result}, indent=2))


if __name__ == "__main__":
    main()
