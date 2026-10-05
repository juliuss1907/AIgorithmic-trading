# Donchian historical holdout checklist

- [x] Instance cost stress and regression tests
- [x] Criteria and evaluator with invalid-evidence tests
- [x] Immutable collector, seal and seven-case runner tests
- [x] Old six-case golden validation plus actual stress dry-run
- [x] Independent review, full regression/build and freeze commit
- [ ] Fresh data collection and strict QA
- [ ] Seven OOS runs, full-journal deterministic verification, verdict first
- [ ] Descriptive report and final handoff (no push/deploy/activation)

Reference batch: `/home/julius/.local/state/aigorithmic-trading/reports/donchian-oos-2022-2024/reference-20261005-01`.
Six legacy summaries/result IDs/journals match exactly. Actual cost2 replay:
net95.97104238716805USDT, DD9.172531787830337%,140 trades. Reference verdict:
Pass, G1-G3 and6/6H. This is a tooling dry-run, not a new-window OOS verdict.
Independent review approved freeze;22 focused tests pass. Wheel/sdist build.
Shared engine/ledger regression:70 tests pass. Full suite:1261 passed,1 failed
in113.66 seconds (only the missing Hermes template described below).
Full-suite known unrelated failure: ignored Hermes `.env.template` absent in
new worktree. No credentials copied and no test skipped to hide that failure.

Freeze engine commit:`34f3d33464749f2f7581c1c74ad45dd3bf9fcbca`.
Seal:`/home/julius/.local/state/aigorithmic-trading/reports/donchian-oos-2022-2024/freeze-20261005-01.json`.

Fresh collection was blocked by strict native timestamps before strategy ran:
all three Spot symbols have a2021-09-29T04:00UTC H4 warmup candle with
closeTime06:59:59.999UTC (3h rather than4h). Exact narrow query reproduces
the source payload; BTC has12 native M15 bars, not16, in that bucket.
Full H4 timestamp audit:6791 bars per symbol, no missing/duplicate opens,
one irregular close each, all outside active OOS. This is timestamp QA only,
not proof that all remaining M15/funding/OHLC checks pass.

No normalization, interpolation, deletion, engine change or new-window replay
was performed. An explicit policy decision is needed for the irregular warmup
bucket. Raw diagnostics are preserved under `diagnostics-20261005-01` in the
same report root. See its `status.md` for evidence and next decision.
