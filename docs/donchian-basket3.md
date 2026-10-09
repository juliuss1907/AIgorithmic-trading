# Three-coin baskets under Setup-2 rules

Julius limits the whole system to three coins and keeps the Setup-2 rule (A4 + ADX20).
This study replays every three-coin basket from BTC/ETH/NEAR/SOL/ZEC with that rule, to
help choose the coins. HYPE is excluded: its Spot history is too short.

**Research on seen data.** Picking the best of ten baskets here is selection, not
out-of-sample evidence. Jev confirmation is not simulated; it can only remove entries.

## Rule and method

- Setup-2 rules unchanged: Donchian30/10 H4, EMA200/50, ADX/DMI14 > 20 rising in both
  markets, VolumeMA20 ×1.2, volume profile, ATR14×3 trailing, Spot60 / Short1x 40,
  combined UTC daily loss 3%, realized sizing, observe-only drawdown.
- Equal thirds per basket in both markets (no weight tuning). `-cost2x` doubles every
  fill's fee and slippage; funding unchanged.
- Control: Setup-2 (NEAR30/SOL40/ZEC30). It reproduced the 2026-10-07 integration check
  byte for byte (result ID, summary and all three journals).
- Frozen five-coin inputs `donchian-adx-five-20261005/approved-data/inputs.json`,
  2022-01-01 → 2026-10-02; every case passed its serialized deterministic repeat.

```bash
uv run python scripts/replay_donchian_adx.py --universe baskets \
  --inputs .../donchian-adx-five-20261005/approved-data/inputs.json \
  --report-root .../donchian-basket3-YYYYMMDD/study-01
uv run python -m intraday.replay_v2.donchian_basket_analysis \
  --comparison .../study-01/runs/comparison.json --output-root .../ranking
```

## Results (sorted by return/drawdown)

| # | Basket | Return % | DD % | Ret/DD | Trades | Win % | PF | Cost×2 % | 2022–24 % | 2024–26 % | Losing halves |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | BTC-NEAR-SOL | 84.24 | 10.55 | 7.98 | 424 | 40.1 | 1.49 | 58.20 | 55.27 | 18.66 | 3/9 |
| 2 | ETH-NEAR-SOL | 81.75 | 11.58 | 7.06 | 423 | 41.4 | 1.47 | 57.39 | 45.07 | 25.28 | 2/9 |
| 3 | ETH-NEAR-ZEC | 66.21 | 9.94 | 6.66 | 409 | 41.6 | 1.44 | 46.13 | 36.77 | 21.53 | 2/9 |
| 4 | BTC-ETH-NEAR | 77.71 | 12.02 | 6.46 | 415 | 39.3 | 1.51 | 52.25 | 41.22 | 25.84 | 1/9 |
| 5 | NEAR-SOL-ZEC | 65.85 | 11.27 | 5.84 | 419 | 42.2 | 1.40 | 46.10 | 43.63 | 15.47 | 3/9 |
| 6 | BTC-ETH-SOL | 70.82 | 12.99 | 5.45 | 425 | 37.2 | 1.42 | 44.54 | 37.73 | 24.02 | 2/9 |
| 7 | BTC-NEAR-ZEC | 66.02 | 12.36 | 5.34 | 410 | 40.2 | 1.45 | 44.81 | 44.37 | 14.99 | 3/9 |
| 8 | BTC-ETH-ZEC | 52.83 | 11.14 | 4.74 | 411 | 37.0 | 1.38 | 31.45 | 24.32 | 22.94 | 3/9 |
| 9 | BTC-SOL-ZEC | 56.36 | 11.95 | 4.72 | 421 | 38.0 | 1.36 | 34.62 | 35.44 | 15.45 | 3/9 |
| 10 | ETH-SOL-ZEC | 55.67 | 11.82 | 4.71 | 420 | 39.3 | 1.35 | 35.18 | 27.07 | 22.51 | 3/9 |
| — | Setup-2 (NEAR30/SOL40/ZEC30) | 66.40 | 10.85 | 6.12 | 419 | 42.2 | 1.40 | N/A | 43.40 | 16.04 | 2/9 |

The 2022–24 column is the old OOS holdout window; 2024–26 is the 703-day window.
Losing halves count full calendar half-years with a negative marked return.

## Reading

- Every basket is profitable in both sub-periods and at doubled costs (+31% to +58%);
  PF 1.35–1.51, drawdown 9.9–13.0%. The rule, not the basket, carries most of the result.
- The five best baskets all contain NEAR. Baskets with ZEC but without NEAR rank last.
- NEAR-SOL-ZEC at equal thirds (#5) is close to the Setup-2 control (+66.40%, DD 10.85%).
- Spreads between neighbours are small relative to sampling noise; the ranking should not
  be read as a forecast.

## Consequences

Choosing a basket other than NEAR/SOL/ZEC needs a new ADR with its own freeze and
prospective paper test; ADR-003 continues unchanged. Runtime work (three-coin limit,
Setup-2 Spot rule with Jev confirmation, Perp short) is a separate plan.

Private artifacts: `donchian-basket3-20261009/study-01/runs/comparison.json`,
`ranking/ranking.json` and `ranking/ranking.md`.
