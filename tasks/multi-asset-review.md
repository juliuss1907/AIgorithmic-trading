# Multi-asset rollout review

Reviewed `main...feature/multi-asset-shadow` on 2026-09-28 before push or deployment.

## Resolved during review

- Legacy snapshot readers remain BTC-scoped; ETH/SOL snapshots cannot mark BTC risk or
  positions accidentally.
- Snapshot persistence writes the owning symbol, and outcome evaluation reads the same
  symbol and market as its journal signal.
- External venue batches persist healthy symbols when another symbol fails and expose
  the failed-symbol list as a partial result.
- Spot soak does not inherit the BTC champion rule. Without an asset-owned Spot rule it
  records a no-setup heartbeat and does not call the model.
- Both no-setup branches attribute soak evidence to the snapshot symbol.

## Verification evidence

- `uv run --frozen pytest -q`: 496 passed.
- `uv lock --check`: passed.
- Docker Compose config validation: passed.
- SQLite copy integrity/schema/lifecycle dry-run: `ok`, schema 20, 12 lifecycle rows.
- Live public smoke: 6/6 symbols succeeded on Binance Spot, Binance USD-M,
  Hyperliquid, Aster, Variational, and Lighter.

## Deliberately not enabled

ETH paper activation, the shared 10% ETH canary risk ledger, asset-owned Spot rules,
and multi-asset open-position projection remain outside this shadow rollout. The runtime
rejects an asset lifecycle in `paper`, so this missing work cannot create a fill.
