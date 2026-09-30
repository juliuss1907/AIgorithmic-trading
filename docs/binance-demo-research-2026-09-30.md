# Binance Demo execution feasibility

Checked on 2026-09-30 against official Binance documentation and the current checkout.
Scope: research and integration recommendations. No credentials were requested or read;
no account was connected, order submitted, or service deployed.

## Verified API capabilities

| Environment | Documented endpoint | Implication |
| --- | --- | --- |
| Spot Demo REST | `https://demo-api.binance.com/api` | Use Spot API routes under this base, e.g. `/api/v3/...`. |
| Spot Demo WebSocket API | `wss://demo-ws-api.binance.com/ws-api/v3` | Separate from Spot Demo market streams. |
| Spot Demo market streams | `wss://demo-stream.binance.com/ws` or `/stream` | Demo execution quotes can be collected separately from research data. |
| USD-M Futures Demo REST | `https://demo-fapi.binance.com` | Use Futures routes under `/fapi/...`. |
| USD-M Futures Demo WebSocket base | `wss://demo-fstream.binance.com` | Verify the applicable market/private stream paths when implementing. |

Sources: [Spot Demo general information](https://developers.binance.com/en/docs/products/spot/demo-mode/general-info)
and [USD-M Futures general information](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info).
The Futures page calls this the testnet platform and says **most**, not all, endpoints
are supported. Actual endpoint and symbol availability must be checked in Demo.

Spot Demo keys are created through [Demo API management](https://demo.binance.com/en/my/settings/api-management),
linked from the Spot Demo documentation. Trading needs the appropriate `TRADE`
permission and signed requests; a read-only key cannot submit orders. Validate Spot
and Futures permissions separately instead of assuming one key has both capabilities.
Sources: [Spot REST security](https://developers.binance.com/en/docs/products/spot/rest-api)
and [Futures general information](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info).

Spot Demo uses market-like prices/books and lets the operator reset balances through
the UI. Binance explicitly distinguishes realistic Demo data from live data. Demo
execution is useful for integration tests; its returns and fill behavior cannot be
treated as a live-market track record.
Source: [Spot Demo comparison with Testnet](https://developers.binance.com/en/docs/products/spot/demo-mode/general-info).

Futures order APIs support placing/querying/cancelling orders, leverage and margin
configuration, trade history, and position queries. Conditional protection is a
separate API: `POST /fapi/v1/algoOrder`. The changelog documents migration of stop,
take-profit, and trailing orders away from the ordinary order endpoint effective
2025-12-09. Do not implement TP/SL using an old tutorial's `/fapi/v1/order` contract.
Sources: [Futures trade API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)
and [Futures changelog, 2025-11-06 entry](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log).

Order timeouts can mean execution status is unknown rather than rejected. Query or
reconcile the original intent before deciding what to do next; automatic blind
resubmission can duplicate exposure. Exchange client IDs are unique among open
orders, so they do not replace a persistent local intent journal.
Sources: [Spot REST timeout handling](https://developers.binance.com/en/docs/products/spot/rest-api),
[Futures HTTP 503 handling](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info),
and [Futures order ID rules](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade).

## Current implementation boundary

`intraday/parent_runtime.py:228` executes authorized targets by calling
`apply_paper_target`. `intraday/parent_paper.py:77` computes fill price from bid/ask
plus synthetic slippage, and computes fees locally. The parent equity/position
state is based on this local ledger. There is no authenticated Binance order
submission in that execution path.

Therefore adding API keys or changing a base URL alone cannot turn current paper
execution into Demo trading. Reuse strategy decisions and deterministic risk rules,
but add an exchange execution backend and exchange-backed account projections.

## Recommended integration boundary

These are project recommendations, not claims that Binance supplies these features
as one ready-made integration.

- Keep explicit modes: decision soak, local simulated paper, and Binance Demo.
  Demo writes must be restricted to documented Demo hosts, with no production
  fallback and no redirects that carry credentials to another host.
- Preserve existing BTC soak evidence and simulated journal records. Give Demo
  orders/fills/positions an account and campaign identity; do not reinterpret old
  simulated fills as exchange executions. A Demo balance reset starts a new
  campaign after reconciliation rather than rewriting historical evidence.
- Persist a risk-authorized order intent before submitting. Record client/exchange
  IDs, status transitions, partial fills, actual reported commission, and exchange
  timestamps. Reconcile startup, reconnects, timeouts, duplicate events, and manual
  account changes. Update exposure from confirmed exchange state, not from an ACK.
- Use Demo balances and a configured strategy capital allocation for sizing. Verify
  available margin, One-way mode, isolated margin, 3x leverage, symbol filters,
  quantity increments, and minimum order value. Detect unexpected existing orders
  or positions before permitting entries; do not automatically change account-wide
  settings when the account is already in use.
- Place and confirm exchange-side Futures protective orders. Keep deterministic
  reduction/flattening paths and explicit recovery for a rejected or missing stop.
  Select stop reference and trigger price from Demo execution data.
- Keep production-market research/context provenance explicit. If research continues
  to use existing public feeds, execution quotes, fills, mark-to-market, and margin
  checks must use Demo data. Record and bound any research/execution price gap.
- Project real Demo open orders separately from positions. Display mode, account
  sync age, fill history, exposure, P&L, fees, and reported funding in CLI/dashboard;
  retain UTC trading timestamps and UTC+7 dashboard history.

Useful exchange account sources: [Futures account API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account),
[Futures position/trade APIs](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade),
and [Futures user stream documentation](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams).
Validate which events, commission/funding semantics, and stream paths are available
in Demo rather than promising full production parity.

## Practical order of work

1. Build the Demo backend with recorded-response tests and disabled order submission.
   Cover uncertain submit results, partial fills, restart recovery, filters, account
   mismatches, and protective-order failure.
2. When account access is needed, have the operator provision Demo credentials on
   the VPS as permission-restricted secret files. Verify server time, identity,
   permissions, balances, existing orders/positions, and BTCUSDT metadata read-only.
   Do not collect API secrets in conversation.
3. After explicit Demo execution activation, validate a bounded BTC Perp order cycle
   including protection, closing/reduction, and reconciliation. Then run the eligible
   BTC strategy in Demo and monitor operational correctness.
4. Add Spot execution when a Spot rule is eligible. ETH and the other assets retain
   their existing rule/evidence gates; Demo support does not waive them or implement
   missing multi-asset risk/accounting support.

The previous decision-only soak remains evidence about decisions/providers and risk
gates. It does not certify the newly built exchange adapter. Demo needs its own
execution acceptance checks before unattended strategy operation.

## Auxiliary sources

[Trading Signal](https://www.binance.com/en/skills/detail/binance-web3/trading-signal)
is a source of on-chain smart-money events on BSC/Solana. It is an optional context
source for mapped tokens, not a ready-made BTC/ETH perpetual strategy.

[Agentic MCP](https://developers.binance.com/en/docs/agent-native/mcp-server/agentic)
documents market/account tools and Spot/Margin/Convert/USD-M/COIN-M trading in a
funded Agentic sub-account. The guide describes real funds and confirmations for
writes; it does not document Demo routing. Use documented Demo exchange APIs for
the deterministic worker. An optional MCP operator integration would be a separate
scope, initially read-only.

Detailed findings and remaining uncertainties are in
[the auxiliary tools research](binance-agent-tools-research-2026-09-30.md).
