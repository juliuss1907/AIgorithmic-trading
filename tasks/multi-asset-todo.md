# Multi-asset implementation checklist

Updated 2026-10-01: v20 seed/ETH-only restrictions below are historical.
Current catalog v23 is dynamic; lifecycle/evidence gates apply independently to all symbols.
See [current architecture](../docs/crypto-intraday-system-design.md).

- [x] Add typed registry for BTC, ETH, HYPE, NEAR, ZEC, and SOL.
- [x] Add per-symbol/per-scope shadow lifecycle and validation.
- [x] Add additive schema v20 migration with BTC backfill.
- [x] Parameterize Binance Spot and USD-M clients.
- [x] Parameterize Hyperliquid, Aster, Variational, and Lighter collectors.
- [x] Persist and report source health per symbol.
- [x] Add `aigt assets list/status` and guarded ETH lifecycle commands.
- [x] Add multi-asset catalog/health/dashboard onboarding projections.
- [ ] Add unified multi-asset Demo positions/orders/PnL projections; `aigt positions` remains legacy BTC paper.
- [x] Add symbol-scoped rules and decision soak for registered Spot 4h/Perp.
- [x] Build Demo multi-route execution journal and shared allocation/risk.
- [ ] Complete supervised exchange order/protection/close acceptance; do not infer it from code tests.
- [x] Prove shadow assets cannot create model calls, signals, or fills.
- [x] Run full tests, lock/Compose checks, migration dry-run, and live smoke tests.
- [x] Stop for review without push or VPS deployment.

Current boundary: code supports catalog symbols independently; no ETH-only capability gate.
Legacy parent simulator remains BTC-only. The historical 10% ETH canary is not the
multi-Demo allocation policy. Demo activation and unified positions remain separate.
