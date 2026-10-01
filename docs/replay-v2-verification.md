# Replay v2 — local verification

Verified 2026-10-01 on `feature/asset-readiness`. No push, merge, VPS deploy,
exchange orders, model calls or trading activation were part of this work.

## Automated checks

- `uv run pytest -q`: **797 passed**, 18 upstream deprecation warnings, 20.64s.
- `uv build`: source distribution and wheel built successfully.
- `git diff --check`: no whitespace errors.
- Tests cover immutable read-only source loading, archived Jev provenance and
  completion timing, next-open Spot execution, Decimal cash/cost accounting,
  ATR sizing, margin, emergency stops and terminal risk guards.
- Regression cases cover missing funding, funding/quote gaps, stale quotes,
  guard crossings caused by exit costs, nonrepresentable prices, atomic/private
  bundles, checksum/symlink rejection, CLI routing and read-only API pagination.

## Browser checks

The separate local preview at `http://127.0.0.1:8083/replay` uses synthetic test
fixtures and its own temporary database/report directory. A visible banner
identifies it as synthetic, not VPS, live market or a Binance account.

- Report list/detail, equity SVG, limitations, UTC+7 timestamps and tables render.
- Detail page has no forms or execution buttons; preview middleware rejects writes.
- HTML/CSS requests returned 200; browser console contained no errors.
- Keyboard Tab reaches the skip link; inspected desktop page had no overflow.
- Mobile/tablet viewport resizing was not verified: the available browser CLI
  did not support the attempted viewport command. Responsive wrapping was
  reviewed in CSS, not claimed as a real-device test.

## Operational isolation

The existing local worker retained this identity and start time before/after:

```text
20838c43c1070ccbf936d22ebf324a62450fe459a4b013764e85ca47407966ac
2026-10-01T07:18:54.245623502Z
```

Source schema remains v23. Existing v1 gates, champion/challenger, soak,
execution configuration and existing dashboard processes were not changed.
Compose changes describe future report-volume mounts only; they were not applied.

This proves local software behavior against fixtures, not strategy profitability
or Binance Demo execution acceptance. Missing funding, unverified historical
instrument filters and non-Demo historical prices remain explicit limitations.
See the [runbook](replay-v2-runbook.md) before running against recorded evidence.
