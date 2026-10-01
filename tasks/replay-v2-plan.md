# Replay v2 implementation

Approved 2026-10-01: offline single-asset research, Spot rule-only and recorded
Perp Jev, Binance profile first, CLI plus read-only web. Not a replacement for
v1 gates, soak or execution acceptance; no activation, VPS deploy or API keys.

## Slices

- [x] Immutable contracts, versioned profile, read-only consistent input loader.
- [x] Spot ledger: next open, ATR/allocation, Donchian/10% stop, portfolio guard.
- [ ] Perp ledger: recorded decisions, quotes, isolated margin, stop/funding.
- [ ] Atomic artifacts and separate replay CLI namespace/Docker routing.
- [ ] Read-only dashboard/API, UTC+7 display, documented limitations.
- [ ] Regression, money/provenance/security/browser checks and local commits.

Defaults: 1000 USDT, weight 1, Spot cap 30%, Perp cap 20%, leverage 3;
daily loss 1.5%, DD 8%; assumed combined costs 15/5 bps per fill (Spot/Perp).
Unknown funding never becomes zero/full net profit. No live adapter calls,
source schema changes or changes to existing evaluators. Reports are stored
separately and are not valid scoped rule evaluation IDs.
