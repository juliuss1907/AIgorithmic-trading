# Hyperliquid frame checksum hotfix

## Scope and incident

VPS release `b9ec479` created frame identity before Pydantic normalized numeric
types. An empty depth bucket produced integer `0`; validation changed it to
float `0.0`, so the checksum no longer matched. The uncaught validation error
escaped advisory capture and Docker restarted the whole portfolio worker.

This hotfix is on `fix/hyperliquid-frame-checksum`, based on that exact VPS
release, without the subsequent Donchian research changes. It changes no trading
rule, gate threshold, credentials, schema version, campaign anchor or activation.

## Behavior and compatibility

- `VenueMarketFrame` and `ExternalObservation` validate a private payload contract
  before hashing it. Public factories, fields, IDs and checksum serialization stay
  compatible; no unsafe construction or checksum bypass is used.
- Numeric fields/maps are normalized consistently. Hyperliquid depth sums start
  at `0.0`; empty buckets are genuine zero depth, not fabricated missing data.
- Nonfinite depth/imbalance is rejected, together with existing malformed market
  and observation guards. Valid historical float payloads retain exact hashes.
- A shadow/off capture catches only `HyperliquidFrameDataError` from the raw-data
  parsing boundary and Pydantic `ValidationError` from the evidence contract.
  Generic `KeyError`, `TypeError`, `ValueError` and arithmetic errors outside those
  boundaries still propagate. Capture skips the bad frame,
  processes remaining assets, and retries normally next cycle. A subsequent good
  frame restores collection without manual reset.
- The same capture boundary covers portfolio soak, portfolio paper and legacy
  shadow capture. Configured `active` mode propagates validation errors instead
  of adopting advisory fallback; existing active policy is not bypassed.
- Database writes remain outside that catch. Database/programming errors are not
  relabeled as harmless feed failures. No frame is written on a capture failure.
- Diagnostic JSON reports component, venue, symbol, UTC time, error code and
  exception type, without exception text, raw payload or secrets. Existing source
  freshness reflects missing frames; no stale frame is made fresh.

Example diagnostic (synthetic):

```json
{"status":"degraded","component":"hyperliquid_capture","venue":"hyperliquid","symbol":"ETHUSDT","at":"2026-10-04T00:00:00+00:00","error_code":"invalid_market_frame","error_type":"ValidationError"}
```

## Verification

- Independent pre-fix checksum tests on `b9ec479`: 68 failures / 40 passes.
  Failures covered all six registry coins, both supported frame venues, all four
  observation sources, empty buckets, round-trips and nonfinite depth.
- Isolation tests failed before the guard, then verified every registry coin can
  fail without blocking the others, single feeds recover, diagnostics are redacted,
  active mode propagates errors, and database/programming failures remain visible.
- A real `HyperliquidFeed` with wrong-coin book data is rejected while a healthy
  second feed still records; a real Binance soak cycle records after shadow failure.
- Sixty real-feed malformed-payload cases cover all six coins (missing numeric
  fields, bad numbers/shapes, crossed book, bad event time, nonfinite/negative
  context). Twenty-four additional wide-book cases cover multiple price scales.
- Final focused suite: **260 passed**. Shadow containment and active propagation
  explicitly exercise both typed parsing errors and genuine Pydantic validation
  failures. Independent review found no remaining required fixes after narrowing
  the catch boundary; its focused run passed 245 tests before final test additions.
- Full suite against final production code: **1,127 passed, 1 failed**, 12 warnings
  in 68.47 seconds. The sole failure is the missing Hermes template below. After
  that run, only test parameterization changed; the final focused suite covers it.
- Read-only VPS compatibility sample at `2026-10-04T10:07:56Z`: 600 historical
  Hyperliquid frames, 950 external observations, 200 feature snapshots. All 1,750
  rows validate and JSON-round-trip. Frames/observations rebuilt by the new factory
  retain their historical identity/checksum. This is a sample, not a full DB audit.
  Sample SHA256: `1b999013e3b6ba5162e6be8139b796d2652ed427dd72fadc5b2df8a94428e551`.
- `uv build` succeeds and `git diff --check` is clean. The clean release worktree lacks gitignored
  `integrations/hermes/trading-ops/.env.template`; the unrelated existing Hermes
  bundle test cannot run successfully there. No secret file was copied, no test
  was skipped/disabled, and no dummy configuration was fabricated to hide it.

Focused verification:

```bash
uv run pytest -q tests/test_venue_frame_hotfix.py tests/test_hyperliquid_capture_isolation.py tests/test_intraday_cross_venue.py tests/test_intraday_hyperliquid.py tests/test_external_context.py tests/test_portfolio_soak.py tests/test_parent_portfolio.py
uv run pytest -q
uv build
git diff --check
```

## Rollout checklist — separate operator approval required

- [ ] Review and explicitly approve push/merge/deploy of the hotfix only.
- [ ] Recheck VPS HEAD, worker identity/start/restart count, campaign IDs/anchors,
  rule hashes, provider health and execution state. Preserve database/report backup
  with the established consistent backup procedure, not a naked live SQLite copy.
- [ ] Build compatible worker/admin/web images and restart worker in a controlled
  deployment. Keep volumes and credentials; do not start/reset soak campaigns.
- [ ] Verify web health/APIs, all enabled collectors, per-coin signals and unchanged
  campaign/rule/execution binding. Jev/LLM spend-limit recovery is a separate issue.
- [ ] Observe 24 hours: restart count does not grow from frame failures; bad feed
  diagnostics identify their coin; healthy assets/Binance continue and failed feed
  resumes on a good frame. Do not manufacture coverage for previous gaps.
- [ ] On regression, rollback images only using the approved procedure. Do not
  restore over the live evidence DB, rewrite checksums or delete historical rows.

No push, deployment, activation or 24-hour VPS acceptance is included in local
implementation completion. HYPE catalog/strategy decisions remain unchanged.
