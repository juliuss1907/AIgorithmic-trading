# Donchian historical holdout criteria v1

Locked before collection. Active UTC window [2022-01-01,2024-10-28T20:00).
This is a retrospective chronological holdout, not prospective proof of edge.
Engine, lockfile, machine criteria and strategy checksums are sealed together.

| ID | Condition |
|---|---|
| G1 | Net A4-30/10 >0 after costs |
| G2 | Net A4-30/10 >0 in a full fee/slippage x2 ledger rerun; actual funding unchanged |
| G3 | M15 marked max DD A4-30/10 <=15% |
| H1 | Fee+slippage(A4) <=0.6*(fee+slippage(A0)); exclude funding |
| H2 | DD(A2) <=0.75*DD(A1) |
| H3 | Net A4-30/10 >= net A4-20/10 |
| H4 | ETH net contribution >0 separately for Spot and Perp in primary |
| H5 | Return%/DD%(A4) >= Return%/DD%(A1) |
| H6 | Return%/DD%(A4) >= Return%/DD%(A3); no automatic fallback |

Pass requires every G and >=4 H. Inconclusive means every G and fewer than
4 H. Any failed G means Fail, including G3. Missing, nonfinite, incomplete,
tampered or non-reproducible evidence means Invalid. A zero denominator is
not_evaluable and never counts as a hypothesis pass. Equality does not pass
strict net-positive conditions. No rounding tolerance changes the thresholds.

Pass/Inconclusive may recommend an operator-approved prospective paper run,
never automatic trading. Fail does not recommend this configuration. DD15%
does not alter the engine's observe-only drawdown policy. No official gate writes.
Old +96.8USDT stress estimate was arithmetic, not evidence for G2.

Event months, annual results, SOL Spot, buy-and-hold, 0% cash, block-bootstrap
intervals and pooled trade statistics are descriptive only. No historical
stablecoin yield is assumed. No tuning or discretionary rerun after verdict.
