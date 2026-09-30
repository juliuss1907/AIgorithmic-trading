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

## Verification and handoff (2026-09-30)

- All five implementation slices completed locally on `feature/execution-binance-demo`.
- Existing simulated-paper regression checks pass; no source schema migration or
  VPS operation was performed. Source read-only behavior is tested against a v22
  fixture, including primary model provenance and tampering/staleness rejection.
- `uv run pytest -q`: **593 passed**, with 18 existing dependency deprecation warnings.
  The new execution test files contain 44 tests covering contracts, transport,
  journal, runtime, source provenance and CLI isolation/recovery.
- `uv build` successfully produced wheel and source distribution in a temporary
  build directory. No dependency or lockfile changes were required.
- Independent review findings on nested API routes, freshness clocks, terminal
  partial/rejected close recovery, and pending entry cancellation were addressed
  and guarded by regression tests. Status snapshots are refreshed after orders
  and flattening, including when stop cancellation remains uncertain.
- Public Demo GET smoke checks succeeded for server clock, BTC filters, and quotes.
  No credentialed account requests, Demo orders, activation, push, or deploy occurred.
- Operator usage and recovery: [Binance Demo runbook](../docs/binance-demo-execution-runbook.md).
  **Next prerequisite:** provision owned 0600 Demo credentials and perform supervised
  account/preflight/order/protection/close acceptance before unattended deployment.

## Follow-up: exchange connect wizard (2026-09-30)

Accepted scope: Binance Demo only now; reserve Binance live and Hyperliquid
testnet/live command syntax without receiving keys. Extend this execution plan;
leave unrelated incomplete soak/Hermes tasks in the global plan/todo intact.

- [x] Reproduce and fix account v3 missing `canTrade`; use signed GET accountConfig,
  strict booleans, and preserve the runtime isolated 3x compatibility gate.
- [x] Add native `aigt connect bnb demo`, hidden input, masked saved-key hint,
  Keep/Replace/Clear default Keep; preserve existing Jev/LLM and explicit paths.
- [x] Validate by GET before atomic credential publication; owned 0600 files,
  private 0700 directory, symlink refusal, cancellation and sanitized errors.
- [x] Guard Replace/Clear with journal lock/read-only checks, paused campaign,
  reconciled intent/fill evidence, flat exchange account and no open orders.
- [x] Use shared default secret path in execution commands; document setup,
  reserved commands, file deletion scope and connection-versus-trading distinction.
- [x] Full regression suite, read-only existing-key smoke, build and review.

No production routing, live execution environment, schema migration, source DB
write, trading activation, leverage mutation, key rotation, push or VPS deploy is
authorized by this follow-up. Clear/Replace are operator-confirmed future actions;
implementation tests use temporary synthetic credentials only.

## Follow-up: multi-asset Spot/Perp Demo (approved 2026-09-30)

Implement the approved multi-asset plan in independently verified slices. Do not
overwrite unrelated global plan/todo items. No VPS deploy, push, order smoke or
trading activation is authorized during implementation.

- [x] Persistent dynamic catalog; independent Spot/Perp onboarding and mappings.
- [x] Demo/Testnet-only discovery with volume, spread, depth and size impact;
  all existing venues visible, only Binance Demo execution selectable.
- [x] Multi-symbol USD-M adapter and Spot balances/filters/fills/native stops.
- [x] Shared allocation and multi-route runtime; per-symbol evidence gates,
  managed-only Spot inventory, durable unknown-order reconciliation.
- [x] CLI and authenticated dashboard wizard with idempotent writes and UTC+7.
- [x] Migration preservation, regression/security/browser tests, build/runbook.

Defaults: Spot USDT and USD-M USDT perpetual; Spot 60% / Perp 40%, effective
notional caps 30% / 20% of total operator capital. Manual coin weights. Spot
long-only, pre-existing inventory excluded. Perp isolated 3x. Keep existing rule,
replay and soak gates; no mainnet fallback and no automatic activation/rebalance.

Slice 1: persistent v23 catalog and registration-aware services/collectors. Symbol
models validate syntax; database boundaries validate membership. Existing static
constants remain seed/legacy defaults, not runtime authority. Verification:
633 tests passed; old candles/checksums/soak migration and upgrade rehearsal pass.

Slice 2: Demo/Testnet discovery, authenticated async scan API, idempotent separate
Spot/Perp add/select flows and `/assets` dashboard. Verification: 642 tests passed,
wheel/source build and desktop Chrome visual smoke passed. Public ETH Spot scan
confirmed Binance and Aster testnet listing; Lighter Spot has an empty testnet book,
so liquidity stays N/A, not invented. Full native browser selection automation
was initially interrupted after a stalled DevTools input operation. A subsequent
isolated Chrome native-input run passed add/scan/select, independent Spot/Perp tabs,
320/768/1440px layouts and zero JavaScript exceptions using synthetic data/token.
No signed live request, activation, order, push or deploy.

Slices 3/4: configurable USD-M symbols, distinct Spot account/transport/contracts,
actual fill and native fee accounting, owned-only Spot inventory, serialized shared
allocation and multi-route execution. Manual route activation verifies exact
symbol/scope champion/replay/soak and primary model provenance. User selected Spot
emergency stop **10%**; Donchian exits and parent loss/exposure limits are unchanged.
Legacy source/journal rows remain preserved; multi configuration requires paused,
flat/reconciled legacy execution and blocks concurrent legacy BTC activation.
Key Replace/Clear checks all account namespaces and both market read capabilities.

Slice 5: optional non-started Docker `demo` profile with read-only source/key mounts,
separate writable journal, no worker dependencies/restart, non-root UID/GID,
read-only root and no capabilities. Compose configuration validated without reading
service environment files. Runbook covers onboarding, weights, per-route gates,
manual activation/recovery and host/container path identity.

Final local verification: `uv run pytest -q` **670 passed** with 18 pre-existing
dependency deprecation warnings; wheel/source build and compile checks passed.
Native Chrome input verified the wizard with synthetic data at 320/768/1440px.
Docker Compose config validated with `/dev/null` env file and `--no-env-resolution`;
no environment secret file was read. No credentialed request, real Demo order,
trading activation, VPS operation, push or merge occurred in this follow-up.
Changes are locally committed on `feature/binance-demo-multi-asset`.

Acceptance boundary: implementation/fake-exchange tests do not prove a real Demo
key's write permission or exchange native-stop/fill behavior. Supervised Demo
order/protection/close acceptance remains a separate operator-authorized step.
Unknown Spot submissions are not inferred from seeded exchange balances; reconcile
the actual order first. Base-fee dust below exchange filters and third-asset fees
are preserved and pause for reconciliation, not silently adopted/sold or valued.

## Prior connect wizard verification (before the multi-asset follow-up)

Verification: **629 tests passed** (18 existing dependency deprecation warnings),
wheel/source build succeeded. Existing local Demo key passed nine guarded GET
requests including accountConfig; credentials unchanged, 5000 USDT, no positions
or open orders, One-way/Single-asset/isolated 5x. The wizard reports the 3x policy
mismatch without changing leverage or activating a campaign. Secret file is 0600;
its dedicated local directory was tightened to 0700 during the first smoke check.
Final code refuses existing nonprivate directories rather than chmod'ing arbitrary
paths. Independent correctness/security review's directory finding was fixed with
a regression test; no other required findings remained.
