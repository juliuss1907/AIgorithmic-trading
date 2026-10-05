# Donchian historical holdout checklist

- [x] Instance cost stress and regression tests
- [x] Criteria and evaluator with invalid-evidence tests
- [x] Immutable collector, seal and seven-case runner tests
- [x] Old six-case golden validation plus actual stress dry-run
- [ ] Independent review, full regression/build and freeze commit
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
