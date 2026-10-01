# Perp information and per-pair leverage control

> Handoff update 2026-10-01: implementation is merged into baseline `29f05c8`;
> source worker/web v23 deployment is recorded in the [current architecture](../docs/crypto-intraday-system-design.md).
> Demo execution/settings profiles were not started in that rollout. Credentialed order/stop/close
> and settings-write acceptance remain unproven. The dated slice restrictions and results below
> are historical evidence, not the current branch/deployment status.


Approved 2026-10-01. Implement on the existing feature branch; no VPS deployment,
live settings change, trading activation, push or merge. Preserve all soak evidence.

- [x] Read-only `aigt perp ETH`: selected route, account/market state, positions,
      mark/entry, actual and configured leverage, unrealized PnL and ownership.
- [x] `aigt perp ETH -leverage` / `--leverage`: integer 1–10, preview then
      Confirm/Cancel. Default 3x. Only paused/flat/reconciled Isolated accounts.
- [x] Shared settings module and additive execution-journal request/audit tables:
      idempotency, fresh confirmation, independent settings-only permission,
      persisted intent before submission, GET-only unknown reconciliation.
- [x] Perp tab dashboard information and keyboard-accessible confirmation dialog;
      authenticated queue consumed by opt-in settings controller, no exchange keys
      in web/browser, stale/unavailable data explicitly labelled.
- [x] Multi-runtime configured per-pair leverage and actual per-pair margin budget;
      no larger notional from larger leverage; drift blocks entries but not exits.
- [x] Focused tests, full suite, real isolated-browser synthetic-data checks,
      package build, runbook and local commits. Real Demo writes remain untested.

Legacy BTC execution remains 3x. Non-3x settings require moving to the multi-route
runtime while flat and paused. Control and trading are separate opt-in processes.

## Verification (2026-10-01)

- Full suite: 725 passed; 18 existing dependency deprecation warnings.
- Focused controller/web/multi-runtime tests: 42 passed.
- Isolated headless Chrome with synthetic account only: Enter preview, default
  Cancel, Escape/no request, rejected 11x, Confirm 3x to 5x, verified read-back;
  320/768/1024/1440px without page overflow and zero JavaScript exceptions.
- Screenshot: `/tmp/aigt-perp-browser-verified.png` (temporary, synthetic data).
- Wheel and sdist built in `/tmp/aigt-perp-build-20261001`; Compose opt-in override
  validated without reading `.env`; `git diff --check` passed.
- Reviewed confirmation races, stale projections/routes, duplicate requests,
  paused/flat/algo-order guards, unknown GET-only recovery, account modes at
  read-back, mixed leverage margin budgets, and read-only evidence preservation.
- Runbook: `docs/binance-demo-execution-runbook.md`.
- Not performed: credentialed Binance settings changes, orders, activation,
  source evidence changes, VPS deployment, push or merge.
