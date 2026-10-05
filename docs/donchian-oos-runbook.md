# Sealed Donchian historical holdout runbook

Run from this research checkout using its locked `uv` environment. These
commands read public historical data and write new local research artifacts.
They do not call models, connect execution accounts, write official gates,
deploy or activate trading. Never overwrite an existing batch.

Plan: [dedicated plan](../tasks/donchian-oos-plan.md).
Criteria: [human](donchian-oos-criteria.md), [machine](donchian-oos-criteria.json).
Keep historical `comparison.json` and all raw snapshots unchanged.

## Old-window validation before fresh data

```bash
uv run python -u -m intraday.replay_v2.donchian_oos_pipeline run \
  --reference \
  --golden /absolute/old-study/comparison.json \
  --inputs /absolute/old-study/inputs.json \
  --output-root /absolute/new-batch/reference
uv run python scripts/evaluate_oos.py \
  --comparison /absolute/new-batch/reference/comparison.json \
  --output-root /absolute/new-batch/reference-evaluation
```

Six base cases must reproduce the old result IDs, summaries and every journal
SHA256. The seventh case actually reruns the ledger with fee/slippage x2.
It is not the old arithmetic stress estimate. Missing stress evidence is Invalid.

Commit reviewed code and locked criteria. Then seal them:

```bash
uv run python -m intraday.replay_v2.donchian_oos_pipeline seal \
  --reference /absolute/new-batch/reference/comparison.json \
  --output /absolute/new-batch/freeze.json
```

The seal binds engine commit, all `intraday/**/*.py` source checksums, scripts,
uv.lock, pyproject, criteria, window, strategy and golden-reference checksum.
No strategy changes after fresh data collection. DD15% only scores G3; the
engine remains observe-only. Any technical correction requires a recorded
reason and a new freeze/batch; never overwrite immutable evidence.

## Collection, seven runs and descriptive report

```bash
uv run python -u -m intraday.replay_v2.donchian_oos_data \
  --seal /absolute/new-batch/freeze.json \
  --output-root /absolute/new-batch/data
uv run python -u -m intraday.replay_v2.donchian_oos_pipeline run \
  --seal /absolute/new-batch/freeze.json \
  --inputs /absolute/new-batch/data/inputs.json \
  --output-root /absolute/new-batch/oos
uv run python -m intraday.replay_v2.donchian_oos_analysis \
  --comparison /absolute/new-batch/oos/comparison.json \
  --reference /absolute/new-batch/reference/comparison.json \
  --output-root /absolute/new-batch/analysis
```

The window is `[2022-01-01T00:00Z,2024-10-28T20:00Z)`, avoiding the known
old H4/M15 mismatch at the excluded boundary. Native H4 warmup600 bars and
native trade M15 warmup1936 bars; mark/funding active window. No interpolation.
Raw snapshots, source checksums, QA flags and funding gaps are inspectable.
Volume profile remains an approximation, not tick-volume-at-price.

Progress shows case names only. Evaluator verdict is announced before PnL.
Each of seven runs has a mechanical full-journal deterministic verification.
For a technical interruption only, repeat the same collection/run command with
`--resume`. Seal/window/input/case checksums must match. Completed cases are
reused, not rerun to tune. Missing final verdict publications are safely restored.

`comparison.json`, `evaluation.json/md`, per-case manifests and full journals
are immutable. `analysis.json` and `report.md` include annual marked equity,
closed-trade accounting, coin/market, exit reasons, stress, event months,
100% Spot buy-and-hold40/30/30, cash0%, seeded5000-draw7/14-day portfolio
block-bootstrap intervals and pooled primary-base trade statistics.
The partial final day remains in replay but not full-day bootstrap samples.
Two independent windows are not presented as one compounded account.

Pass/Inconclusive only inform an operator decision about prospective paper.
Fail means do not paper this configuration. No automatic activation or A3 fallback.

## Verification

```bash
uv run pytest -q tests/test_donchian_oos_*.py
uv run pytest -q
uv build
git diff --check
```

The fresh worktree currently lacks the ignored Hermes `.env.template`; the
unrelated distribution test can therefore fail. Do not copy credentials or
hide this baseline failure to make research tests appear green.
