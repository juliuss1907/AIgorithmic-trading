# Modular Binance Demo execution

Scope accepted 2026-09-30: a small common execution interface, the existing paper
simulator as one adapter, and Binance Demo as another. Start with BTC USD-M Perp;
future venues implement the same order/account contracts. No automatic rollout or
activation is part of building this module.

## Acceptance

- Existing paper behavior and BTC soak evidence remain compatible.
- Exchange order/account contracts identify venue, environment, account, and symbol.
- Binance signed requests can reach only the USD-M Demo host. Credentials are loaded
  from a regular 0600 secret file, redacted, and never accepted on command arguments.
- Persist each immutable order intent before network submission; retries/restarts
  reconcile unknown results rather than submitting a duplicate.
- Record confirmed fills, partial fills, open orders, and confirmed positions.
- Require a passing portfolio evaluation and a clean, compatible Demo account before
  activation. Default disabled. Account settings are checked, not silently changed.
- Native stop orders are tracked and reconciled. Missing/failed protection blocks
  entries and exposes an operator recovery path.
- CLI provides preflight, status, activation, pause, run, and flatten with explicit
  execution state and source database paths. Demo orders never enter the simulated
  paper ledger.
- Demo runtime consumes fresh primary BTC Perp evidence, reapplies current rule/risk
  gates against the Demo account, and performs deterministic exits ahead of entries.
- Execution persistence uses a separate SQLite journal. The existing v22 source DB
  is read-only for this worker, so no migration or soak-worker restart is required.
- Tests cover Demo routing/signing, safe defaults, uncertain submission, restart,
  duplicate intent/events, partial fills, configuration mismatch, and protection.

## Slices

1. Typed execution contracts and simulator adapter; prove paper regression parity.
2. Binance Demo transport, normalized orders/account/filter/protection behavior.
3. Durable coordinator and isolated execution journal with reconciliation.
4. BTC Perp runtime and CLI; source evidence stays read-only.
5. Documentation, contract/recovery tests, full suite, independent review.

## Deliberately deferred

Spot Demo execution, Hyperliquid execution, concurrent multi-venue capital allocation,
WebSocket optimization, and a new dashboard page. Read-only CLI projections cover
the module's initial operational inspection. Existing market/news collectors remain
unchanged. Live Demo permissions and fill semantics require a credentialed smoke
test after the operator provisions Demo keys.
