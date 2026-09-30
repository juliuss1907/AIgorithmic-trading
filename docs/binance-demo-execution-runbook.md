# Binance Demo execution module

Supports dynamically registered **USDT Spot and USD-M USDT Perpetual** instruments
listed on Binance Demo. Perp requires One-way, Single-asset, isolated 3x per coin.
Hyperliquid and other DEX execution remain unavailable. This module does not turn
on the current paper worker, deploy to the VPS, or submit an order just because a
key exists. The older BTC-only commands are retained as an explicitly legacy path.

## Multi-asset workflow (new)

Add Spot and Perp independently; listing in one market does not enable the other:

```bash
uv run aigt assets add DOGE --market spot --database state/intraday/intraday.sqlite3
uv run aigt assets add DOGE --market perp --database state/intraday/intraday.sqlite3
uv run aigt assets scan DOGE --market spot --notional 1000 --database state/intraday/intraday.sqlite3
uv run aigt assets venue set DOGE --market spot --venue bnb --environment demo --database state/intraday/intraday.sqlite3
```

Interactive `add` scans and offers a venue choice; non-interactive calls never
auto-select. `--no-scan` registers only. `venue set` rescans unless a fresh
`--scan-id` is supplied (60-second validity). No selection activates trading.
Existing seeded BTC/ETH/etc also need explicit Demo venue selection before using
the multi-route runtime. Existing BTC soak evidence is not reset or copied to
other symbols. The catalog source schema is additive v23; migration/rehearsal must
be applied before enabling the new runtime on an old deployment.

Dashboard: `/assets`, separate Spot/Perp tabs, volume in the venue's quote currency,
spread/depth 5/10/25 bps and buy/sell impact for the requested quote notional.
UI history timestamps are UTC+7; exchange bars and journals remain UTC.
Write operations require the existing dashboard **control token**, not a Binance
key; the browser does not persist it. Scans are async, writes idempotent.
Only Binance Demo can be selected. Hyperliquid/Aster/Lighter use verified testnet
metadata where available; Variational stays `testnet_unverified` until a verified
public testnet is provided. Empty/malformed/timeout liquidity is N/A, not zero or
mainnet data. Depth is a snapshot/lower bound, not a fill guarantee.

Configure total strategy capital and manual weights **within each sleeve**:

```bash
uv run aigt execution demo configure \
  --source-database state/intraday/intraday.sqlite3 --capital 1000 \
  --spot-weight ETH=0.5 --spot-weight DOGE=0.25 \
  --perp-weight ETH=0.5 --perp-weight SOL=0.5 --spot-stop-percent 10
```

This performs read-only exchange checks and leaves the portfolio paused. Each
sleeve's weights sum to at most 1; unused weight stays reserve, never automatically
redistributed. Spot budget is 60%, Perp 40%, with effective notional caps 30% / 20%
of operator capital. In the example ETH Spot is capped at 150 USDT, DOGE Spot at
75, ETH Perp at 100 and SOL Perp at 100. ATR can reduce Spot sizing further.
Spot's operator-selected emergency native SELL stop is **10% below actual entry**;
Donchian exit stays independent. Portfolio daily loss 1.5%, drawdown 8%, gross
50% and isolated-margin 10% remain unchanged and can close positions sooner.

Preflight and activate **each route** with its exact passing soak evaluation:

```bash
uv run aigt execution demo preflight --multi --symbol ETH --market spot \
  --source-database state/intraday/intraday.sqlite3
uv run aigt execution demo activate --multi --symbol ETH --market spot \
  --source-database state/intraday/intraday.sqlite3 --evaluation-id SPOT_SOAK_ID
uv run aigt execution demo activate --multi --symbol ETH --market perp \
  --source-database state/intraday/intraday.sqlite3 --evaluation-id PERP_SOAK_ID
uv run aigt execution demo run --multi --once --source-database state/intraday/intraday.sqlite3
uv run aigt execution demo status
uv run aigt execution demo pause --multi
uv run aigt execution demo flatten --multi
```

The sample IDs are placeholders, not real evidence. New routes require their own
champion, passing replay and latest passing soak. Only BTC Perp may use its legacy
passing BTC portfolio evaluation. Spot uses native closed 4h bars with 8h/1d context
and consumes each eligible bar once. Perp requires a fresh verified primary model
decision. Signals remain research/mainnet observations; **execution quotes and
discovery are Demo/Testnet**, labelled separately and guarded for dislocation.

The multi journal separates Spot and Perp account namespaces; the shared portfolio
retains capital, cash baselines and risk watermarks across restarts and pauses.
Legacy BTC evidence/journal rows are preserved; pause, flatten and reconcile the
legacy runtime before configuring multi. Once configured, legacy activation/run
is blocked for that journal. Allocation changes require paused, flat, reconciled
state; capital/source/risk history cannot be reset by reconfiguration.

Spot pre-existing balances are excluded from strategy equity and ownership. Only
confirmed AIGT fills create inventory; SELL and protection are bounded to it.
Base/USDT fees are accounted in native units. Third-asset fees, unknown orders,
unresolved stop cancellation or filter-sized dust pause for reconciliation instead
of inventing balances, selling seeded holdings or replaying a timed-out submission.
Spot stops lock coins, so confirmed stop cancellation/reconciliation precedes
market SELL. Perp protection stays reduce-only and is retained while closing.

`connect bnb demo` reports Spot and Perp read capabilities separately. Successful
connection does not prove order-write permission. Replace/Clear require both
markets readable, portfolio paused, no managed Spot inventory, no Perp position,
no exchange open orders and all intent/fill evidence reconciled.

### Optional Docker profile, not an automatic rollout

`demo-execution` is behind the `demo` profile, has no dependency on/restart of the
soak worker, reads `intraday-state` read-only, and writes a separate host journal.
The key directory is read-only; it runs without capabilities and with a read-only
root filesystem. Host journal/key directories must already exist, be private and
owned by the selected `AIGT_EXECUTION_UID/GID` (defaults 1000/1000). Verify the source
DB/WAL is readable by that UID; do not change ownership of the soak volume blindly.
It does not inherit `.env.intraday` or provider keys. `restart: no` is intentional
for supervised acceptance. Missing directories/keys/allocation cause refusal.

After supervised order/protection/close acceptance and explicit deployment approval,
the operator may build/start **only** `demo-execution` with the `demo` profile.
Configure/activate using the same container source/journal paths (host and container
source paths are intentionally not silently interchangeable). Stop only this
service for rollback; keep journal, keys and all soak data. No profile has been
started as part of implementation.

## Legacy BTC-only compatibility path

The sections below describe the retained original BTC Perp workflow, not the
multi-route workflow above. They cannot run against a journal configured for multi.

### Boundaries

```text
existing v22 soak DB -- read-only evidence --> BTC Demo runtime + deterministic gate
                                                   |
                                         durable OrderCoordinator
                                                   |
                                         BinanceDemoAdapter --> Demo API
                                                   |
                                         separate execution journal
```

`intraday/execution/contracts.py` defines order intents, updates, actual fills,
account/position/quote/filter contracts, `ExecutionAdapter`, and `TradingVenue`.
`simulated.py` implements the same order interface for the current paper simulator;
its parent ledger still owns simulated accounting. A future venue implements these
contracts; it does not replace signal generation or deterministic risk rules.
Venue-specific signing, filters, and conditional orders stay inside that adapter.
`DemoRuntime` is the initial BTC integration, not a general multi-venue capital router.

Demo orders/fills never enter `parent_paper_fills` or the old paper portfolio.
The Demo worker reads the **existing source database with SQLite `mode=ro`** and
does not construct `IntradayStore`, migrate v22, or restart the soak worker.
No changes to existing collectors, LLM calls, soak evidence, or source schema are needed.

The standalone commands below deliberately bypass registered Docker deployment
routing. Run from an installed checkout (`uv run aigt ...`), with an explicit source
database path. Do not start them inside the existing soak container. A dedicated VPS
service/Compose profile is a subsequent rollout, after credentialed acceptance.

## Credentials — provision locally, never paste into chat

Create a **Binance Demo** API key with USD-M Futures trading permission, not a live
Binance key and not merely a read-only key. No withdrawal permission is needed.
See [Demo API management](https://demo.binance.com/en/my/settings/api-management).
This adapter accepts HMAC API key/secret pairs, not RSA/Ed25519 credentials.

Set account modes/margin/leverage manually in Binance Demo. Preflight checks these
settings and **does not change them**. Use only this strategy on the account;
manual trading, deposits, balance resets, or non-USDT commissions cause drift checks
to pause entries. Rotating the key changes the local account fingerprint: pause,
reconcile, and flatten before rotation; retain the previous execution journal.

Run this locally on the intended host, from the installed project checkout:

```bash
uv run aigt connect bnb demo
```

The wizard asks for API key and secret using hidden terminal input, checks the
Demo API with **GET only**, and then saves the pair to
`state/execution-secrets/binance-demo.json`. The JSON format remains compatible
with files created by the previous Python provisioning snippet. Files are owned
0600, in a private owned 0700 directory; symlink paths and echo fallback are refused.
Failed API checks do not save new credentials or replace the old pair.
The wizard creates a new secrets directory with mode 0700, but never changes the
permissions of an existing directory automatically. If the old provisioning
snippet created the default secrets directory with 0755, explicitly run
`chmod 700 state/execution-secrets` first; do not chmod the project/home directory.

When a valid local file already exists:

```text
Binance Demo API key: abcd... ✓
  [K]eep / [R]eplace / [C]lear (default K):
```

The hint shows at most four initial key characters, never the secret. ✓ means
**a local credential file exists**, not that the current API check passed.

- **Keep / Enter:** reuse the saved pair and check the API without rewriting it.
- **Replace:** confirm, input both new values, validate, then atomically replace.
  Existing campaigns/journal remain unchanged; key rotation needs explicit activation.
- **Clear:** confirm and remove only this credential file. The Binance key is not
  revoked, and execution/soak journals are not deleted. The file removal has no undo;
  reconnect with the original pair or create a new key in Binance.

Replace/Clear first require any recorded campaign to be paused, every execution
intent/fill to be reconciled, and a live GET check to show no positions or regular/algo
orders. A busy journal lock, unavailable API, or malformed journal blocks changes.
Pause, flatten, stop the dedicated execution runner, then manage the key. Use
`--execution-database` to identify the actual journal if it is not the default.

`connected` means account reads succeeded; leverage 5x or unsupported account modes
are configuration warnings, not invalid keys. The wizard does not need a champion,
source database, or passing soak. It does not activate trading or change settings.
Account-level `canTrade` is read from `/fapi/v1/accountConfig`, not account v3; it
does **not** prove that this API key has order-submission permission.

Both connect and execution commands default to this credential file. Existing
`--secrets-file` overrides continue to work; use a dedicated secrets directory,
not the project root or home directory. Exchange connect commands always run
on the current host, never inside the registered Docker admin/soak service.

| Command | Current support |
| --- | --- |
| `aigt connect bnb demo` | Binance Demo Spot and USD-M credential capability probes |
| `aigt connect bnb` | Reserved for Binance live; currently refused |
| `aigt connect hl demo` | Reserved for Hyperliquid testnet; currently refused |
| `aigt connect hl` | Reserved for Hyperliquid live; currently refused |

Unsupported commands do not prompt, save credentials, or access any exchange.
Use `aigt`, not `aight`. Existing `connect jev` / `connect llm` are unchanged.
Do not put secrets into command arguments or `.env`; `state/` is Git-ignored.
The application never prints signed request URLs or Binance error bodies.

## Read-only preflight

Replace the source path with the actual database path on the intended host. The
source DB can be actively written by the existing soak worker while Demo reads it.

```bash
uv run aigt execution demo preflight \
  --source-database state/intraday/intraday.sqlite3 \
  --execution-database state/execution/binance-demo.sqlite3 \
  --secrets-file state/execution-secrets/binance-demo.json
```

This uses only exchange GET requests. It checks clock skew, trading permission,
account modes, BTC metadata, Demo quotes, current positions/open regular and algo
orders, and the source BTC Perp champion. `clean_account` must be true before first
activation. It cannot prove an actual order/protective-stop cycle works in Demo;
that requires the separate supervised acceptance step below.

REST is fixed to `https://demo-fapi.binance.com`; there is no production fallback,
user-configurable host, or credential-bearing redirect. The adapter uses ordinary
`/fapi/v1/order` for MARKET orders and `/fapi/v1/algoOrder` for native stops.
See [official Futures trade API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)
and [Futures Demo general information](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info).

## Explicit activation and supervised run

Activation is an **operator action**, not an automatic consequence of passing soak.
Use a passing BTC portfolio evaluation ID from the source database. The latest
portfolio evaluation must also remain passing. Capital is a USDT allocation, not
the account's entire Demo balance, and is capped at 10000. A small allocation can
be too small for BTC minimum notional after the Perp sleeve allocation (20%).

```bash
uv run aigt execution demo activate \
  --source-database state/intraday/intraday.sqlite3 \
  --execution-database state/execution/binance-demo.sqlite3 \
  --secrets-file state/execution-secrets/binance-demo.json \
  --evaluation-id EVALUATION_ID --capital 1000

uv run aigt execution demo run --once \
  --source-database state/intraday/intraday.sqlite3 \
  --execution-database state/execution/binance-demo.sqlite3 \
  --secrets-file state/execution-secrets/binance-demo.json
```

Activation records the campaign and risk baseline; it submits **no orders**. `run`
can submit orders after activation. `--once` runs one reconciliation/risk/decision
cycle, **not a guaranteed round-trip trade**. Only a verified, fresh primary numeric
BTC Perp model signal under the activated champion can open a position. Stub/shadow,
stale or checksum-mismatched evidence is rejected. Current provider fingerprint
and the original successful model-call identity are verified. Market features and
Demo quotes must be fresh, research/Demo mark gap ≤1%, Demo spread ≤10 bps.

The strategy reuses `ScopedEntryGate` and parent policy. Sizing starts from confirmed
exchange equity, capped by operator capital. Available margin and Decimal instrument
filters are checked. First rollout has no pyramiding or same-tick flips. Protection
and deterministic loss exits run before reading entry evidence, including while
entries are paused. The native mark-price stop remains on Binance when the process
or network stops. A triggered algo stop is reconciled through its child order and
actual trade fills; an ACK or `FINISHED` label alone is never a synthetic fill.

For a continuous foreground worker, omit `--once`; default interval is 15 seconds
(allowed 10–300). This does not manage the existing worker or call models again.

## Status, pause, and flatten

```bash
uv run aigt execution demo status \
  --execution-database state/execution/binance-demo.sqlite3

uv run aigt execution demo pause \
  --execution-database state/execution/binance-demo.sqlite3

uv run aigt execution demo flatten \
  --execution-database state/execution/binance-demo.sqlite3 \
  --secrets-file state/execution-secrets/binance-demo.json
```

`status` reads the **local last-synced** account snapshot, campaign controls, recent
orders with actual fills/fees, and fill count. Check `last_sync_at`; it is not a
live exchange refresh. Status and pause require no keys or source DB. `pause`
blocks entries and retains native stops; keep the runner operating for monitoring.
If multiple accounts are journaled, use the fingerprint from status with `--account-id`.

`flatten` pauses entries and submits a reduce-only close, then cancels journaled
protective stops after flatness is confirmed. It does not need the source database
or a healthy LLM. It never means “order definitely filled” until exchange status and
positions agree. Do not stop monitoring until status confirms flat and all relevant
orders are reconciled. Ctrl-C stops the runner; it does **not** flatten.

Existing `aigt positions` and dashboard still describe **local simulated paper**,
not Demo exchange positions. Use `execution demo status` for this module. Exchange
journal timestamps are aware UTC; this module does not change dashboard UTC+7 history.

## Recovery and evidence

- Every immutable client intent is stored before submission. UNKNOWN results are
  queried by the same client ID, including after restart. **Never blindly resend.**
- Actual fills are deduplicated and keep exchange commission currency and realized
  P&L. Account wallet is reconciled against those fills plus reported funding.
  Account resets/manual balance changes are not treated as strategy profit.
- A known partial entry is canceled/reconciled before reduction. A terminal partial
  or rejected close permits a new close intent for the observed residual; a live or
  UNKNOWN close never permits a duplicate submission.
- If an entry ACK/lookup is unavailable but a same-side, bounded BTC position is
  observed, runtime tries reduce-only native protection, pauses, and retains the
  entry as UNKNOWN; it does not invent fills from positions. If protection or cancel
  cannot be confirmed, inspect Binance Demo immediately. No software can guarantee
  a close while the exchange is unavailable. An unresolved possible entry blocks
  automatic flatten/re-entry because it could still fill later.
- A failed/missing stop pauses entries and attempts a confirmed reduce-only close.
  An unknown stop cancel blocks new entries even when flat. Check the original
  client ID in Binance Demo; continue reconciliation, not manual journal edits.
- Re-activation requires a clean flat account and all intents resolved. It retains
  campaign capital, cash baseline and risk history; it does not reset losses.
  Champion changes require explicit re-activation. Balance reset/key rotation/new
  campaign must be coordinated manually after flatness and archival of prior
  evidence; never delete or overwrite a journal to “fix” a mismatch.

Use one execution journal per dedicated account, one runner, and no duplicate
services. The local lock excludes simultaneous cycle/activation/flatten operations;
pause may report a busy lock during an exchange cycle; retry after that cycle finishes.
Full order/fill history is
retained locally for audit; this initial low-frequency BTC module is not intended
for high-frequency or multi-venue operation. Income/trade pages are bounded at
1000 records and fail closed when pagination would be required.

## Acceptance before unattended VPS operation

Automated fixture tests cover signing/host restriction, disabled defaults, actual
account/quote API routing, partial fills, conditional child orders, idempotency,
unknown results/restart, drift, native protection failure, and source read-only
provenance. **They are not credentialed Binance Demo acceptance.**

Before an unattended service is deployed, an operator must provision Demo keys,
complete read-only preflight, explicitly activate, and supervise a bounded eligible
entry → native stop confirmation → reduce-only close → fill/account reconciliation.
Also verify stop endpoint support/permissions, funding/commission semantics, and
no pending orders remain. Futures Demo supports most, not necessarily all, production
endpoints. Preserve the existing VPS soak worker throughout rollout.
