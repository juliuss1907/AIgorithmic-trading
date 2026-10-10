# Offline A/B: numeric vs compact Jev state

Question (2026-10-10): should the Jev state change from numbers to categorical tokens, as in
the alex saint article (twelve words, no decimals)? The system already has `compact_v1`
(`intraday/providers.py::compact_state_tokens`, twelve fixed categorical tokens with thresholds
owned by the code) and a paired shadow experiment, but the VPS never ran it: 115,141 numeric
calls and no compact call, because the shadow branch lives in the paper worker and the VPS runs
the soak worker.

With Setup-2, Jev is called a few times per week, so collecting the stock 1,000 live pairs would
take decades and cost is no longer a reason. The compact state is a pure function of an immutable
stored snapshot, so a past decision can be re-asked in both encodings and scored against the
realized 15-minute forward return.

## Run

```bash
# Plan only: sample composition and a cost estimate, no network, nothing written.
uv run python -m intraday.replay_v2.compact_ab --report-root /tmp/unused --dry-run

# Real run (paid model calls; needs OpenRouter credit and the provider secrets file).
uv run python -m intraday.replay_v2.compact_ab \
  --report-root ~/.local/state/aigorithmic-trading/reports/compact-ab-YYYYMMDD \
  --per-coin 500 --max-calls 8000 --max-usd 1.0
```

Run it where the provider secrets are mounted (the VPS admin container). The source database is
opened read-only; model calls are journaled in `scratch.sqlite` inside the report directory.

## Method

- Sample: numeric primary `perp_intraday` signals with a matured 900 s outcome, one per 30
  minute bucket and coin, non-hold buckets preferred (at most two thirds of a coin's quota),
  deterministic by hash. Natural stratum mix is reported and used for a re-weighted view.
- Every sample is re-asked with the same model at the same time: the numeric state again (noise
  floor and model drift control) and the compact state. The stored numeric answer is kept.
- Snapshots are re-verified against the stored state; mismatches are skipped and counted.
- Scoring reuses `decision_evaluation`: accuracy with the 0.20% dead band, Brier, ECE,
  agreement, actionable flips, token reduction, availability; a bootstrap 95% interval on the
  accuracy difference; confirmation counts and precision at confidence 0.85.
- Verdict uses the stock criteria (accuracy not worse than 2 pp, Brier not worse than 0.02,
  token reduction at least 15%, availability at least 95%, at least 1,000 pairs). The 14 day
  duration criterion is waived (collection-time only). `eligible` additionally requires the
  interval's lower bound to exclude a regression worse than 2 pp; otherwise `inconclusive`.

## Limits

It measures the 15 minute `perp_intraday` direction question, not the H4 Setup-2 confirmation;
the data spans about 13 days; small differences are indistinguishable from noise. The result is
evidence for the operator and changes nothing by itself. A follow-up shadow on live Setup-2
signals (descriptive only) needs a `spot_4h` mapping in `compact_state_tokens` and an outcome
horizon for `spot_4h` first.
