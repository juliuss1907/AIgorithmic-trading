# ADR-004: Three-coin system with the Setup-2 rule

## Status

Accepted 2026-10-09 as operator policy. Not implemented: the runtime still runs the catalog's
Jev-driven soaks. The implementation needs its own plan once the initial basket is chosen.
ADR-003 (prospective paper test of NEAR/SOL/ZEC) continues unchanged.

## Context

Running every catalog coin (six Perp decision soaks) spread attention and, when OpenRouter
credit ran out on 2026-10-07, cost every soak its heartbeat coverage. Jev costs about
$0.17 per coin per day, so the limit is about focus more than cost. The ten-basket study
([donchian-basket3](../donchian-basket3.md)) found every basket profitable under the Setup-2
rule; NEAR appears in the top five, and NEAR/SOL/ZEC at equal thirds ranks fifth.

## Decision

- **At most three coins in the whole system.** Soaks, Jev calls and trading are limited to the
  active set; other coins stay in the catalog, observation only, with their evidence kept.
- **Rule:** Setup-2 (A4 + ADX20: Donchian30/10 H4, EMA200/50, ADX/DMI14 > 20 rising,
  VolumeMA20 ×1.2, volume profile, ATR14×3 trailing). The rule generates the signal and Jev
  confirms it, as the Spot runtime does today.
- **Per-coin mode and allocation:** each active coin runs `spot`, `perp` or both, with its own
  Spot and Perp weights and a Spot/Perp capital split (Setup-2 default 60/40). A coin counts once
  toward the limit whatever its mode. Each market's weights sum to at most 1.
- **Changes only when paused and flat:** coin, mode or weight changes require the portfolio to
  be paused with no open position, as `execution demo configure` already requires. No automatic
  rebalance.
- **Fast coin rotation without a backtest:** a new coin needs catalog add and scan, at least
  600 closed H4 bars on each enabled market (plus M15 data for the volume profile), a soak of at
  least 14 days and a passing gate v2 for each enabled market, then an audited operator switch.
  Readiness labels it `no_backtest_evidence` until a backtest exists. Missing history blocks it
  with `history_not_ready`.
- **Sequence:** research first (done for the five frozen coins), runtime after.

## Alternatives considered

- Limit only concurrently open positions while soaking every coin: keeps cost and attention spread.
- Require a backtest before every coin switch: stronger evidence, but newly listed coins (HYPE has
  about 15 days of Spot history) could not rotate in for months.
- Rebalance or apply new weights to open positions: adds costs; Setup-2 was tested without rebalancing.

## Consequences

The initial basket must be chosen (keep NEAR/SOL/ZEC, or another; a different basket needs its
own freeze if a prospective test is wanted). ADR-003 says a runtime port follows a prospective
`pass`; building the runtime before that is an explicit operator choice to record when the
implementation plan is approved. The Perp short side needs new runtime work (H4 Perp candles,
deterministic Perp parameters, a gate path not built on recorded Jev decisions).
