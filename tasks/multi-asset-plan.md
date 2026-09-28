# Multi-asset shadow and ETH reference pipeline

Add ETH, HYPE, NEAR, ZEC, and SOL as Binance Spot/Perp assets without changing the
running BTC decision path. All five assets start in `shadow`; ETH is the only new
asset allowed to advance to decision soak and paper after independent gates.

## Locked decisions

- BTC keeps its current lifecycle, evidence, positions, and risk policy.
- ETH, HYPE, NEAR, ZEC, and SOL enable both Spot and Perp market-data collection.
- ETH is end-to-end capable but starts in shadow. The other four remain data-only.
- Binance is the primary venue; Hyperliquid, Aster, Variational, and Lighter are
  read-only evidence sources.
- Parent risk remains shared. A future ETH paper canary is capped at 10% gross equity;
  existing global limits remain unchanged.
- Delivery stops after local tests and migration dry-run. No push or VPS deployment.

## Delivery order

1. Typed asset registry and lifecycle contracts.
2. Additive schema v20 ownership and compatibility migration.
3. Parameterized, failure-isolated multi-asset collectors.
4. Asset status CLI and dashboard projections.
5. ETH-scoped decision, soak, activation, journal, and parent-risk support.
6. Full regression, migration dry-run, live public-data smoke tests, and review.

## Compatibility and rollback

Schema changes are expand-only. Existing BTC rows are backfilled as `BTCUSDT`, legacy
BTC readers remain valid during the shadow window, and no old table is removed. Before
ETH creates a paper position, rollback may use the old runtime or the verified pre-upgrade
backup. After ETH paper activation, ETH must be flattened/disabled before an old runtime
is restored.
