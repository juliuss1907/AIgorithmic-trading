# Binance auxiliary agent tools research

Checked on 2026-09-30. Scope: the two auxiliary links supplied by the operator. Research only; no tools installed, accounts connected, keys inspected, or orders submitted.

## Trading Signal: on-chain context

The [Binance Skills Hub page](https://www.binance.com/en/skills/detail/binance-web3/trading-signal) describes individual smart-money wallet buy/sell events on BSC (`56`) and Solana (`CT_501`). This is token activity on those chains, not a signal service for Binance BTCUSDT/ETHUSDT Spot or USD-M perpetual contracts. Chain support does not imply native SOL itself is covered by any particular returned event.

The [official CLI reference](https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/trading-signal/references/cli.md) documents:

- Identity: `signalId`, `chainId`, `contractAddress`, `ticker`.
- Trigger/context: `direction`, `smartMoneyCount`, `signalCount`, `signalTriggerTime`, `timeFrame`, token tags.
- Price/activity: trigger/current/peak price and market cap, `totalTokenValue`, `maxGain`, `exitRate`, `status`.
- Pagination: `page`, `pageSize` capped at 100; optional `smartSignalType`.
- Rate-limited business response: `100004`. No numerical requests-per-second limit is documented in that reference.

The [official implementation](https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/trading-signal/scripts/cli.mjs) sends a JSON POST to:

```text
https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/web/signal/smart-money/ai
```

Its request uses no API key, signature, or wallet authorization; the CLI has a ten-second timeout and accepts only the two documented chain identifiers. That verifies the supplied client is unauthenticated; it does not guarantee availability from every IP/region. The reviewed files publish no API pricing, service-level guarantee, retention guarantee, or fixed polling quota.

There is a documentation normalization detail: the [skill page](https://www.binance.com/en/skills/detail/binance-web3/trading-signal) describes `maxGain` as a percentage string, while the [CLI reference](https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/trading-signal/references/cli.md) calls it a decimal fraction (`0.25` means 25%). The reference also lists `valid` in addition to `active`, `timeout`, and `completed`. An adapter should preserve raw values and verify live samples before choosing normalization.

Recommendation (project inference): useful as an optional shadow context source for on-chain tokens, with mapping by chain and contract address rather than ticker alone. It has limited immediate relevance to the current six-coin CEX Spot/Perp universe. Historical peak gain is observed after the trigger; replay must use the snapshot received at decision time, not later peak/exit information.

## Agentic MCP: account/operator interface

The [official Agentic MCP documentation](https://developers.binance.com/en/docs/agent-native/mcp-server/agentic), modified September 29, 2026, documents a hosted server at `https://agent.binance.com/mcp/agentic`. It supports Spot, Margin, Convert, USD-M and COIN-M Futures; public market data; account balances/positions/bills; and transfers between wallets within an Agentic sub-account. Withdrawal scope is unavailable. Main-account access can be read-only; initial funding is performed manually. Documented writes, including cancellations, require user confirmation. The example trade spends real funds.

Authorization uses the Binance browser consent flow with selected scopes, without local API keys. The guide distinguishes public market data from authenticated account/trade scopes. It does not publish tool names/signatures, trading fees, rate quotas, or a Demo/Testnet selector. Main-to-sub-account manual transfers are described as fee-free; that does not establish fee-free trading. Neither [the MCP introduction](https://developers.binance.com/en/docs/agent-native/mcp-server) nor this setup guide establishes support for Demo credentials or endpoints. Treat Demo support as unverified, not impossible and not supported by inference.

Recommendation (project inference): an optional read-only operator/research interface, after supported authentication and actual tool discovery. Our deterministic worker's unattended Demo execution should use the documented Demo exchange APIs and our own risk/order/reconciliation adapter. MCP capabilities alone do not establish order idempotency, restart recovery, protective-order support, or Demo routing for our worker.

## Verification limits

- Browser extraction successfully read the two official pages and the Binance-owned GitHub skill/reference/source files. Search results from community projects were not used as evidence.
- Live Web3 data was not fetched; endpoint availability, field units, and native-coin coverage remain untested.
- The MCP server was not registered or authenticated; actual `tools/list` and runtime scope enforcement remain untested. Public OAuth metadata URLs could not be opened by the web tool, so no exact OAuth version, grant specification, or token lifetime is claimed.
- These notes do not authorize enabling real-account MCP trading or replacing the portfolio readiness gates.
