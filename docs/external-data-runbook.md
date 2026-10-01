# External data runbook

## Safety model

Binance public/mainnet data remains the research/signal reference venue.
Demo discovery/execution uses separate Demo/Testnet quotes without mainnet fallback. Hyperliquid, Aster, Variational, Lighter,
CryptoRank, and Leviathan are read-only evidence sources. Their observations are stored
and shown on the dashboard, but they do not change portfolio decisions or hard-risk
gates. A missing, stale, malformed, or rate-limited source degrades only that source.

Dynamic catalog source schema v23 controls enabled symbols/scopes and venue mappings;
missing support is reported, not invented. Read-only collectors do not imply execution
support. Only Binance Demo execution is currently selectable. System boundaries:
[canonical architecture](crypto-intraday-system-design.md).

## Sources and cadence

| Source | Data | Default cadence |
| --- | --- | --- |
| Hyperliquid | Per-symbol book, mark/oracle, funding, OI for supported mappings | live book + 30 s metadata |
| Aster | mark/index basis, funding, book depth, latest liquidation | 60 s + liquidation stream |
| Variational | long/short OI, funding, TVL, executable spreads by size | 5 min |
| Lighter | mark/index, base and USD OI, funding, spread, volume | 60 s |
| CryptoRank | Fear & Greed, Altcoin Season, global cap and volume | 1 hour |
| Leviathan | approved, non-sponsored news | news cycle, 30 min |

The dashboard endpoint `/api/dashboard` exposes these under `external_sources`. Status is
`missing`, `healthy`, or `stale`; source timestamps and local receive timestamps remain
separate for replay and latency inspection. `aigt portfolio soak report` includes a
bounded health projection without raw payloads. External observations retain 30 days.

## Configure CryptoRank

The public DEX sources and Leviathan need no credential. CryptoRank is optional and is
enabled only when its key file exists.

```bash
install -d -m 700 state/source-secrets
install -m 600 /dev/null state/source-secrets/cryptorank-api-key
# Edit that file and place exactly one CryptoRank API key on the first line.
aigt restart
```

The worker mounts `state/source-secrets` read-only. The web container does not mount it,
and neither logs nor dashboard responses contain the key. At the default hourly cadence,
the three Sandbox endpoints consume 72 credits per day.

## Verify

```bash
aigt status
aigt logs --tail 200 --no-follow
curl -fsS http://127.0.0.1:8081/api/dashboard
```

Look for scheduler jobs named `external_aster`, `external_variational`,
`external_lighter`, and `external_cryptorank`. Do not promote a new source into decision
features until its replay/soak evidence has been reviewed and a separate promotion rule
has been implemented.
