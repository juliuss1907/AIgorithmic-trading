# Replay v2 gates and Binance cost profiles

Approved 2026-10-01. Implement locally on `feature/replay-v2-gate`; no VPS
deployment, trading activation, remote push, or mutation of existing soak evidence.

## Decisions

- Every registered Spot 4h / Perp coin; no ticker whitelist.
- Spot: fee 10 bps + slippage charge 5 bps per fill.
- USDT Perp market fills: taker fee 5 bps + additional slippage 5 bps;
  bid/ask spread implicit, actual historical funding settlements separate.
- Net return > 0, drawdown < 8%, existing daily guard 1.5%, Spot stop 10%.
- Historical pass permits explicit operator start of fresh 14-day validation;
  never automatic promotion/execution. Preserve v1 evidence and old profiles.
- Spot history >=2190 native bars, coverage >=99%, >=6 closed trades;
  historical window begins after first 1095 bars. Historical Spot rule-only.
- Perp decision collection >=14 days, >=100 outcomes, >=95% outcome/heartbeat
  and quote coverage, >=6 closed trades, complete funding/provenance.
- Spot validation retains >=6 matured 12h setups and >=95% coverage.
  Perp post-gate validation >=14 days, >=100 new outcomes, >=6 closed trades.

## Slices

- [x] Versioned separated costs; preserve legacy output; ledger regression tests.
- [x] Immutable funding history collection and validation, public data only.
- [x] Pure gate evaluator, append-only evaluations and campaign binding.
- [x] Explicit CLI lifecycle, readiness/dashboard version/cost/blocker display.
- [x] Runbooks, focused tests, full suite, build and read-only local evidence.

## Rollout boundary

Schema extension must read v23 and preserve all market/rule/soak records. Any
future deployment requires verified backup and upgraded writers before migration.
No running campaign is reset. A v1 rejection remains historical evidence even
when an operator explicitly chooses a passing new v2 campaign for the same rule.

Source schema stays v23; the optional gate extension has its own version 1 and is
installed only by explicit gate writes, never by readiness/read-only projections.
Verification: [evidence](../docs/replay-v2-gate-verification.md). Full suite 833
passed; build succeeded. Frozen BTC local gate passed (+1.90%, DD 3.93%, 14 trades)
as of the 2026-10-01 snapshot; this is not a current VPS evaluation or soak start.
