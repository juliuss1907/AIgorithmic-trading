# ADR-006: Build the three-coin Setup-2 runtime before the prospective verdict

## Status

Accepted 2026-10-10 by the operator. Implementation in three stages (A active set and caps,
B Spot Setup-2, C Perp 4H short); each stage is tested and committed separately and deployed
only with a separate approval after a verified backup.

## Context

[ADR-004](004-three-coin-system.md) set the policy: at most three coins, Setup-2 signals
confirmed by Jev, per-coin mode and weights, changes only when paused and flat, fast rotation.
[ADR-005](005-eth-near-sol-prospective-paper.md) froze ETH/NEAR/SOL for a prospective paper
test whose verdict is expected around June 2027. ADR-004 requires an explicit record when the
runtime is built before that verdict. The operator chose to build it now.

The current runtime calls Jev every 30 s per coin for `perp_intraday` (six coins, about
$1/day of OpenRouter credit), has no coin limit, lets BTC call Jev without any stage check,
and caps Demo sleeves at 30%/20% of capital with a 50% gross and 10% margin limit.

## Decision

- **Stage A (active set):** an append-only, audited active set in the source database
  (`intraday/active_set.py`, `aigt active-set show|init|switch`). With no version recorded,
  behaviour is unchanged. Once a version exists:
  - only its coins' rule scopes (`spot_4h`, later `perp_4h`) may call Jev; every other coin,
    BTC included, keeps collecting market data and records `skipped_inactive` per H4 slot;
  - `perp_intraday` and `spot_daily` are off for every coin; their evidence is kept;
  - LLM auto-proposals, weekly confidence reviews and weekly gates skip inactive scopes;
  - a switch requires a paused and flat Demo portfolio and a flat, paused BTC parent paper
    portfolio; coins rotating in need 600 contiguous H4 bars and Setup-2 gate evidence per
    enabled market, but no backtest (readiness labels `history_not_ready`, `no_backtest_evidence`).
- **Caps follow the split:** a Setup-2 Demo allocation (`profile=setup2_v1`) binds the active-set
  version, copies its weights and 60/40 split (`execution demo configure --from-active-set`), and
  derives gross (100%), Perp (40%) and isolated-margin (40% at 1x) limits from it. Portfolio halts
  move to 3% daily loss and 15% drawdown for this profile only. A new active-set version pauses
  entries until a flat reconfiguration. Legacy allocations keep their caps and serialization.
- **Rule details for stages B and C:** indicators anchored at the ADR-005 freeze minus 600 H4
  bars so live signals match the weekly prospective replay; ATR×3 trailing stop capped at 10%
  from entry; the Setup-2 soak needs 14 days and 95% heartbeat but not six matured setups (the
  rule fires about once per 25 days per coin and market); replay gates need at least six trades,
  net profit and drawdown below 15%; the Perp short requires Jev SELL or STRONG_SELL.

## Alternatives considered

- Wait for the ADR-005 verdict: months without a runtime path for the chosen system.
- Keep `perp_intraday` beside the rule-driven Perp for comparison: more cost and complexity.
- Keep the legacy caps: live sizes about a quarter of the researched Setup-2 exposure.

## Consequences

Live results will differ from the prospective test even when signals match: Jev removes some
entries, fills follow the Jev answer, stops are capped at 10% and sizing does not compound.
Deploying stage A ends all Jev-driven Perp soaks; positions in BTC paper and `perp_intraday`
must be flat first. Stage C needs a source schema rebuild to v24, deployed to all containers
together.
