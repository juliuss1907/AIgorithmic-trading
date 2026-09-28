# Multi-asset implementation checklist

- [x] Add typed registry for BTC, ETH, HYPE, NEAR, ZEC, and SOL.
- [x] Add per-symbol/per-scope shadow lifecycle and validation.
- [ ] Add additive schema v20 migration with BTC backfill.
- [ ] Parameterize Binance Spot and USD-M clients.
- [ ] Parameterize Hyperliquid, Aster, Variational, and Lighter collectors.
- [ ] Persist and report source health per symbol.
- [ ] Add `aigt assets list/status` and guarded ETH lifecycle commands.
- [ ] Add multi-asset dashboard and positions projections.
- [ ] Add ETH-scoped rules, soak, journal, and shared parent-risk support.
- [ ] Prove shadow assets cannot create model calls, signals, or fills.
- [ ] Run full tests, lock/Compose checks, migration dry-run, and live smoke tests.
- [ ] Stop for review without push or VPS deployment.
