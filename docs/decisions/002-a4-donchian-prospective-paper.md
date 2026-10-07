# ADR-002: Prospective paper test of frozen A4-Donchian30-10

## Status

Superseded 2026-10-07 by [ADR-003](003-setup2-prospective-paper.md): the operator switched the
basket to NEAR/SOL/ZEC. Weekly runs stop; the one run (2026-10-02 → 2026-10-06, 0 trades,
`insufficient_sample`) remains as evidence. Originally accepted 2026-10-06, before any
post-freeze data was replayed. Research only: no orders, no runtime rule, no gate or activation change.

## Context

Of 187 backtested setups, A4-Donchian30-10 is the only family with both seen-data
and out-of-sample evidence that stays consistent:

| Period | Return | Max DD | Trades | Profit factor |
|---|---:|---:|---:|---:|
| 703 days 2024-10-29 → 2026-10-02 (seen) | +15.91% | 8.46% | 140 | 1.38 |
| OOS 2022-01-01 → 2024-10-28 | +34.87% | 10.47% | 204 | 1.49 |
| OOS with doubled costs | +24.92% | 11.35% | 204 | 1.34 |

The OOS verdict was Inconclusive (3/6 hypotheses, DD just above 10%) and only allows
an operator to consider prospective paper. The runtime Spot rule lacks the A4
EMA/ADX/volume/volume-profile filters, and runtime Perp follows Jev decisions, so the
case cannot be soaked through the existing gate without new build work.

## Decision

Replay the frozen case on data that did not exist when it was chosen.

- **Rule:** `FilterConfig(entry_window=30, exit_window=10, filter_level=4)` with all
  other fields at their defaults: 60/40 Spot long / isolated Short1x, BTC 40 / ETH 30 /
  SOL 30, realized sizing, ATR14×3 trailing, 3% daily loss, observe-only drawdown.
  Rule hash (every field except start/end):
  `4a8eae77d90bd8a3e207f558ecae700e6b4fd4f4672d358d7a26e55a7b21096b`.
  Replaying this config on the frozen 703-day inputs reproduces the published
  `donchian-filters-20261006-rerun` A4-Donchian30-10 result ID, summary and journals byte for byte.
- **Freeze:** 2026-10-02 00:00 UTC. Every run replays from the freeze to the latest
  published H4 boundary. Warmup comes from the frozen bundle; only post-freeze data is fetched.
- **Criteria**, judged once the window is at least 120 days **and** has at least 60 closed trades:
  - profit factor ≥ 1.2;
  - max drawdown ≤ 12%;
  - net PnL > 0;
  - net PnL > 0 after adding every fill's fee and slippage once more (an estimate, not a re-replay);
  - the replay reconciles (`status = complete`).
  Before the sample minimum the verdict is `insufficient_sample`. Criteria, rule and freeze
  are not changed after seeing results; a changed rule needs a new ADR and a new freeze.
- **Cadence:** run weekly by hand (`intraday/replay_v2/donchian_prospective.py`
  `collect`, then `evaluate`), each into new directories under the private reports root.

## Alternatives considered

- Activate or Demo-trade now: OOS was Inconclusive; not enough evidence.
- Pick a tuned ADX threshold (18/20): chosen on data that includes the OOS period.
- Port filters to runtime first: large build before prospective evidence exists.
- Spot-only Donchian or Donchian + Short1x reserve: good 703-day results but no OOS yet;
  candidates for a later challenger.

## Consequences

A `pass` only justifies porting the A4 filters into the runtime Spot rule as an opt-in,
default-off profile and sending it through replay, gate v2 and a ≥14-day soak. It does
not enable trading. A `fail` retires this case. Each run closes open positions at the
window end, so recent results are marked rather than realized. The engine labels every
report `window_already_seen_not_untouched_out_of_sample`; for these runs the
`evaluation.json` freeze and rule hash are the authoritative description.
