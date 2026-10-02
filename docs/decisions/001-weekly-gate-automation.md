# ADR-001: Weekly gates independent of trading transitions

## Status

Accepted; implemented locally 2026-10-02. Deployment/activation require separate approval.

## Context

Enabled Spot/Perp routes need repeatable evaluations as evidence accumulates.
Daily replay repeats accounting without changing evidence requirements; manual-only
runs miss eligibility changes. Rules, collection clocks and campaigns must survive
migration/restarts.

## Decision

Use optional market-wide seven-day policy, immutable v2 candidate selection and
durable per-phase jobs in the existing source DB. Poll in a background loop independent
of collectors. First evaluate when minimums are ready; subsequent runs wait at least
seven days after the latest non-pass v2 evaluation, including manual runs. Keep
cumulative windows. OS lock serializes manual/automated gates; cutoff/evaluation ID
reconcile crashes. Public funding collection precedes offline Perp accounting.

Stop the stage on pass and notify operator. No auto validation, tuning, model calls,
promotion or execution activation. Preserve v1 evidence but forbid v1 admission on
selected routes, including when paused.

## Alternatives considered

- Daily replay: redundant work and confusing repeated pass notifications.
- Weekly clock reset: loses cumulative coverage/samples.
- LLM-operated gates/promotion: violates deterministic gate/risk boundary.
- New queue service or automatic DB restore: unnecessary coordination/evidence loss.
  Additive SQLite jobs and policy pause suffice.

## Consequences

Future enabled coins inherit policy but still need supported data, rules and evidence.
Pass does not enable trading. Rejected validation/hard-risk/binding failures require
operator intervention. Optional tables preserve v23 readers; writers must be upgraded
before opt-in. Rollback pauses scheduling without removing bindings or v2 admission.
