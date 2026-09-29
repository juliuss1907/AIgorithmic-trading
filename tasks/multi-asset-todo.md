# Multi-asset implementation checklist

- [x] Add typed registry for BTC, ETH, HYPE, NEAR, ZEC, and SOL.
- [x] Add per-symbol/per-scope shadow lifecycle and validation.
- [x] Add additive schema v20 migration with BTC backfill.
- [x] Parameterize Binance Spot and USD-M clients.
- [x] Parameterize Hyperliquid, Aster, Variational, and Lighter collectors.
- [x] Persist and report source health per symbol.
- [x] Add `aigt assets list/status` and guarded ETH lifecycle commands.
- [ ] Add multi-asset dashboard and positions projections.
- [ ] Add ETH-scoped rules, soak, journal, and shared parent-risk support.
- [x] Prove shadow assets cannot create model calls, signals, or fills.
- [x] Run full tests, lock/Compose checks, migration dry-run, and live smoke tests.
- [x] Stop for review without push or VPS deployment.

Current boundary: ETH can advance independently from shadow to model-backed soak and
journal evidence. Asset-specific paper positions, the shared 10% ETH canary risk ledger,
and multi-asset position projection remain intentionally unchecked above.
