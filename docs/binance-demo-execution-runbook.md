# Binance Demo execution module

Initial scope: **BTCUSDT USD-M Perp**, one dedicated Binance Demo account, One-way,
Single-asset USDT, isolated margin, 3x leverage. Spot Demo and Hyperliquid execution
are not implemented yet. This module does not turn on the current paper worker,
deploy to the VPS, or submit an order just because a key exists.

## Boundaries

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

To create the dedicated owned 0600 JSON file, run this locally on the intended host:

```bash
uv run python - <<'PY'
import getpass
import json
import os
from pathlib import Path

secret_path = Path("state/execution-secrets/binance-demo.json")
secret_path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "api_key": getpass.getpass("Binance Demo API key: "),
    "api_secret": getpass.getpass("Binance Demo API secret: "),
}
descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w") as output:
    json.dump(payload, output)
print("Demo credential file created; contents not printed.")
PY
```

The file must belong to the running Unix user, be regular (no symlink), and have
permission 0600. Existing files are not overwritten. Do not put secrets into
command arguments or `.env`; `state/` is already Git-ignored. The application
redacts credentials and never prints signed request URLs or Binance error bodies.

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
