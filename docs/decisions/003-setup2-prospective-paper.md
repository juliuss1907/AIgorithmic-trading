# ADR-003: Prospective paper test of frozen Setup-2 (NEAR/SOL/ZEC)

## Status

Accepted 2026-10-07, before the freeze and before any post-freeze data was replayed.
Supersedes [ADR-002](002-a4-donchian-prospective-paper.md). Research only: no orders,
no runtime rule, no gate or activation change.

## Context

The operator chose to paper-test NEAR/SOL/ZEC instead of BTC/ETH/SOL, with ADX20 and
NEAR 30 / SOL 40 / ZEC 30. That is exactly Setup-2 of the ADX allocation study
([donchian-adx-setups](../donchian-adx-setups.md)):

| Window 2022-01-01 → 2026-10-02 (seen data) | Value |
|---|---:|
| Final equity / return | 1,663.98 USDT / +66.40% |
| Max drawdown (engine, M15 marks) | 10.85% |
| Closed trades / winners | 419 / 177 (42.2%) |
| Profit factor | 1.40 |
| Six-month periods with a loss | 2 of 10 (−2.23%, −5.21%) |

After merging the ADX research branches onto the fixed engine (2026-10-07), a replay from the
same frozen inputs reproduced every Setup summary and journal byte for byte; only result IDs
changed with the evaluator version. The frozen rule hash below equals the study's Setup-2 config.

Setup-2 was chosen after seeing 2022–2026 data, including the 2022–2024 OOS period, so it
has no clean out-of-sample evidence. Only prospective data can provide it.

## Decision

- **Rule:** `ADXSetupConfig(setup=2, **specification(2))` from
  `intraday/replay_v2/donchian_adx_setups.py`. 60% Spot long and 40% isolated Short1x, both
  NEAR 30 / SOL 40 / ZEC 30. Closed-H4 Donchian 30/10 with all A4 filters: EMA200/EMA50,
  ADX/DMI14 with ADX strictly above **20** and rising, Volume MA20 ×1.2 and Volume Profile.
  ATR14 sizing and ×3 trailing stop, 3% combined daily loss, observe-only drawdown, realized sizing.
  Rule hash (every field except start/end):
  `a9c7140a83e185d6dc0ff05734df7089bf645d8a8f7d7ff1fe677bb9eac7df04`.
- **Freeze:** 2026-10-08 00:00 UTC. Each run replays from the freeze to the latest published
  H4 boundary. Warmup and post-freeze data come fresh from Binance public APIs; any data gap
  fails the collection, and none of the historical NEAR/ZEC source exceptions apply.
- **Criteria**, judged once the window is at least 120 days **and** has at least 60 closed trades:
  - profit factor ≥ 1.2;
  - max drawdown ≤ 12%;
  - net PnL > 0;
  - net PnL > 0 in a full replay with every fill's fee and slippage doubled (`cost_multiplier=2`);
  - both replays reconcile (`status = complete`).
  Before the sample minimum the verdict is `insufficient_sample`. The rule, freeze and criteria
  do not change after seeing results; a changed rule needs a new ADR and a new freeze.
  At roughly 0.24 trades per day, the 60-trade minimum is expected around June 2027.
- **Cadence:** run weekly by hand (`donchian_prospective collect`, then `evaluate`), each into
  new directories under the private reports root.

## Alternatives considered

- Keep the BTC/ETH/SOL A4 test (ADR-002): it has OOS evidence, but the operator prefers this basket.
- Same basket with ADX25: the original A4 definition, but the operator chose ADX20.
- Activate or Demo-trade now: no clean out-of-sample evidence.

## Consequences

ADR-002 stops; its one run (2026-10-02 → 2026-10-06, 0 trades) stays as evidence. A `pass`
only justifies porting the filters into the runtime Spot rule as an opt-in, default-off profile
that goes through replay, gate v2 and a ≥14-day soak. A `fail` retires this case. Each run
closes open positions at the window end, so recent results are marked rather than realized.
