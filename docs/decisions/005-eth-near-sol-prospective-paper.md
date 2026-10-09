# ADR-005: Prospective paper test of the frozen ETH-NEAR-SOL basket

## Status

Accepted 2026-10-09, before the freeze and before any post-freeze data was replayed.
Supersedes [ADR-003](003-setup2-prospective-paper.md). Research only: no orders, no runtime
rule, no gate or activation change.

## Context

[ADR-004](004-three-coin-system.md) limits the system to three coins under the Setup-2 rule.
From the ten-basket study ([donchian-basket3](../donchian-basket3.md)) the operator chose
ETH/NEAR/SOL at equal thirds:

| Window 2022-01-01 → 2026-10-02 (seen data) | Value |
|---|---:|
| Net return / max drawdown | +81.75% / 11.58% |
| Closed trades / win rate / profit factor | 423 / 41.4% / 1.47 |
| Doubled fill costs | +57.39% |
| 2022-01 → 2024-10 / 2024-10 → 2026-10 | +45.07% / +25.28% |
| Losing full half-years | 2 of 9 |

The basket was picked as the second of ten on seen data, so it has no clean out-of-sample
evidence. Only prospective data can provide it.

## Decision

- **Rule:** `BasketConfig(basket=('ETH','NEAR','SOL'), **basket_specification(...))` from
  `intraday/replay_v2/donchian_adx_setups.py`. Setup-2 rules: 60% Spot long and 40% isolated
  Short1x, equal thirds in both markets, closed-H4 Donchian 30/10 with all A4 filters
  (EMA200/EMA50, ADX/DMI14 with ADX strictly above 20 and rising, Volume MA20 ×1.2, volume
  profile), ATR14 sizing and ×3 trailing stop, 3% combined daily loss, observe-only drawdown,
  realized sizing. Rule hash (every field except start/end):
  `bf6fccc9dc3cc1148c45f42a9b43bff4a80221a9b68230f40a18c6675cd51c88`. It equals the studied
  ETH-NEAR-SOL case.
- **Freeze:** 2026-10-10 00:00 UTC. Each run replays from the freeze to the latest published
  H4 boundary with fresh public Binance data; any data gap fails the collection.
- **Criteria** (same as ADR-003), judged once the window is at least 120 days **and** has at
  least 60 closed trades: profit factor ≥ 1.2; max drawdown ≤ 12%; net PnL > 0; net PnL > 0 in a
  full replay with every fill's fee and slippage doubled; both replays reconcile. Before the
  minimum the verdict is `insufficient_sample`. Rule, freeze and criteria do not change after
  seeing results. At about 0.24 trades per day, the minimum is expected around June 2027.
- **Cadence:** the existing local weekly timer (Monday 12:00 Vietnam time) runs
  `donchian_prospective scheduled` and sends the verdict to Telegram.

## Alternatives considered

- Keep NEAR/SOL/ZEC (ADR-003): prospective evidence would not match the basket chosen for runtime.
- Run both baskets: two weekly verdicts to follow for one decision.
- No prospective test: runtime soak alone (14 days) is far shorter evidence.

## Consequences

ADR-003 stops before its first run (its freeze was 2026-10-08; the first timer run was due
2026-10-12). A `pass` supports the ADR-004 runtime build; a `fail` retires this basket under
this rule. Each run closes open positions at the window end, so recent results are marked
rather than realized.
