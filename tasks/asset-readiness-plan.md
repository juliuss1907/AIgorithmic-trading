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

- [ ] Read-only store and pure shared Spot-soak/Perp-replay previews; preserve evaluator semantics.
- [ ] Per-symbol/scope projection: lifecycle, champion/candidate, history, evaluations,
      progress, blockers, current-phase timer lower bound and next action.
- [ ] CLI/API: filters, readable table/JSON, routing, safe database errors.
- [ ] Extend `/assets` catalog with keyboard-accessible details, refresh/empty/error
      states; UTC+7 display, UTC source; prevent stale tab responses.
- [ ] Focused tests, browser checks (320/768/1024/1440), full regression, review and docs.

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
