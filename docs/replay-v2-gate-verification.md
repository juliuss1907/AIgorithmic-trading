# Replay v2 gate — implementation evidence

Implemented locally on `feature/replay-v2-gate`, 2026-10-01/02. This is not a VPS
deployment or trading-activation record. See [runbook](replay-v2-runbook.md) and
[approved plan](../tasks/replay-v2-gate-plan.md).

## Verification

- `uv run --frozen pytest -q`: **833 passed**, 18 upstream deprecation warnings.
- `uv build`: wheel and source distribution built successfully.
- `git diff --check`: clean.
- After the final template/CSS correction, focused web/status tests: **8 passed**;
  package build succeeded again.
- Costs tested separately and charged once; legacy profile shape/output preserved.
- Funding tests cover pagination, checksums, wrong symbol/nonfinite values,
  empty history and timeouts; no credentials or order endpoint used.
- Append-only gate/campaign tests preserve v1 rejection and raw evidence;
  future/old/wrong-profile evaluations, active-route conflicts, unknown extension
  versions and report tampering fail closed.
- Spot end-to-end validates new 14-day samples, manual promotion and exact Demo
  evidence binding; promotion leaves the route in soak, not paper/trading.
- Perp end-to-end uses synthetic verified archived decisions, dense quotes and
  funding across two disjoint 14-day windows: collection then fresh validation.
  This proves code semantics, not real strategy profitability or exchange support.
- Readiness and research HTTP reads do not run replay/model/order calls or migrate
  source DB. CLI status retains legacy evidence and labels v2 separately.

## Frozen BTC check — historical, not a live gate on VPS

On 2026-10-01, a read-only export of the existing frozen research snapshot was
evaluated on an isolated local database, using the unchanged BTC Spot 30/8 rule.
Only the local subset database received a new v2 evaluation.

| Item | Observed result |
| --- | --- |
| Snapshot as-of | 2026-10-01 11:58:25.134046 UTC |
| Frozen source checksum | `a89912f43e16331c6a584e5c588d23ea6636b2ec784d5e7b80602ae6d037331b` |
| Candidate | `btcusdt-spot_4h-candidate-d674a4c72b7e689ada98` |
| Local gate | pass · `a66c0a7f50a9c1ef75549e1627f2ba6a` |
| Research run | `180e2c03fc754b0396797aedc55fd19d` |
| Native 4h history | 2198 bars |
| Net return | +1.898771% |
| Maximum drawdown | 3.927340% |
| Closed trades | 14 |
| Exchange fees | 8.418858 USDT |
| Assumed slippage | 4.209429 USDT |
| Hard-risk violations | 0 |

The profile charges Spot 10+5 bps per fill: the total remains 15 bps, not a fee-only
10 bps scenario. This continuous account replay is not equivalent to v1 folds or
an independent OOS test. Passing it does not prove the Spot Jev filter, Demo fill
availability, account fee tier or operational reliability.

The existing v1 rejection and candidate status were preserved. VPS source checksum
and worker container identity/start time were checked unchanged before/after the
read-only operation. No VPS gate evaluation, validation start, reset, deployment,
promotion or exchange order was performed. A later rollout must run a fresh gate
against a current verified snapshot and start a new campaign explicitly.

## Browser verification

Chrome DevTools MCP was unavailable; fallback used isolated Chromium headless/CDP,
not the user's browser profile. The frozen BTC report was visually checked on
2026-10-01. After temporary files were cleared by the environment change, a new
synthetic ETH fixture was used for local responsive checks on 2026-10-02.

Read-only DOM/network/accessibility checks cover `/assets` and `/replay/RUN_ID`
at 320/768/1024/1440 px. Gate/version and 10+5 bps labels render, source times are
displayed UTC+7, and research pages contain no activation controls. No credentials,
browser storage or user sessions were accessed. Temporary previews are not actual
VPS dashboards and their state must not be used for operator decisions.

The checks found long limitation-code text overflowing the replay page at 320 px;
the list now wraps with `overflow-wrap: anywhere` and has a regression assertion.
Assets has an explicit empty favicon to avoid a harmless 404. The last completed
CDP check had no console or HTTP errors. A final cache-disabled browser rerun after
the CSS patch was interrupted by the environment permission change; a subsequent
attempt was blocked by restricted localhost socket access. Do not interpret the
focused template tests as a completed post-patch browser check.

## Rollout still pending

Verified backup, upgraded relevant writers/CLI/execution readers, deployment and
fresh gate/validation are separate actions. Demo account/venue/settings/allocation
and supervised protection/reconcile/close acceptance remain required. No push,
merge, deploy or trading activation is included in this implementation.
