# Asset readiness — implementation plan

Approved 2026-10-01. Baseline `85aef90`, source v23.
Read-only rule gates and venue projection for dynamic Spot/Perp catalog; no Demo
account checks, model/network calls, activation or VPS deployment.

## Contract

`aigt assets readiness [COIN] [--market spot|perp] [--json] [--database PATH]`
and public `GET /api/assets/readiness?symbol=COIN&market=spot|perp` share one
versioned projection. CLI preserves existing assets Docker routing.
Preview is not a persisted evaluation. Venue selection is separate from rule
gates. No `can_trade` field or mutation buttons.

## Ordered slices

- [x] Read-only store and pure shared Spot-soak/Perp-replay previews; preserve evaluator semantics.
- [x] Per-symbol/scope projection: lifecycle, champion/candidate, history, evaluations,
      progress, blockers, current-phase timer lower bound and next action.
- [x] CLI/API: filters, readable table/JSON, routing, safe database errors.
- [x] Extend `/assets` catalog with keyboard-accessible details, refresh/empty/error
      states; UTC+7 display, UTC source; prevent stale tab responses.
- [x] Focused tests, browser checks (320/768/1024/1440), full regression, review and docs.

## Safety and acceptance

- Reads never initialize/migrate source or persist evaluations. Test schema/content
  equality and write prohibition; missing/old database returns an explicit error.
- Spot uses stored replay (never runs backtest on GET); 365-day history then
  exact passing replay/start-soak, 14 days/95% heartbeat/6 matured 12h setups.
- Perp uses exact scoped campaign/cutoff, 14-day decision soak then separate
  72-hour post-replay validation. Preview metrics/status match existing evaluators.
- Include champion legacy, rejected/no-rule/disabled and inconsistent registry states.
  Earliest time is a lower bound, not promised completion or trading permission.
- No replay/risk threshold changes, evidence rewrite, auto-actions, push/merge/deploy.
- Keep other open tasks in the main plan/checklist intact.

## Verification — local implementation, 2026-10-01

- `uv run --frozen pytest -q`: **746 passed**, 18 dependency deprecation warnings.
- Shared preview/evaluation parity; post-replay samples cannot reuse pre-cutoff
  evidence; 72-hour boundary and Spot history staleness boundary covered.
- Source content preserved for API/CLI; read-only writes prohibited; missing/v22
  databases never initialized/migrated; reader snapshot remains stable across a writer.
- BTC Perp legacy champion preserved; non-BTC champions missing evidence and
  invalid registry targets reported as inconsistent, not legacy exemptions.
- Headless Chrome/CDP with a separate synthetic source/profile: 320/768/1024/1440,
  no document overflow, keyboard disclosure, rapid Spot/Perp switches, error/empty/retry,
  GET-only requests and no runtime exceptions. Screenshots visually inspected.
  Local evidence: `/tmp/aigt-readiness-check-876k4c/browser-results.json` and screenshots.
- `node --check` for both asset scripts; `git diff --check`; CLI exercised against
  synthetic source only. No new dependency, schema migration or risk threshold.
- Correctness/readability/architecture/security/performance review completed.
  Unrelated user session file preserved. No source runtime, credentials, order,
  VPS, push, merge or activation changes. Rollout remains an operator-approved step.
