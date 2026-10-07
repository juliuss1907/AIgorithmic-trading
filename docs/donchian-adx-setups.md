# ADX portfolio setup samples

Five additional setups requested after reviewing the five-coin sensitivity study.
This is local historical research on already-seen data, not untouched OOS or
trading approval. No push, merge, deployment, model calls or trading activation.

Window: `[2022-01-01, 2026-10-02)` UTC. Each setup starts with 1,000 USDT and keeps
capital and positions continuous across the entire window.

| Setup | Spot allocation | Short allocation | ADX |
|---|---|---|---|
| 1 | 60%; BTC26/ETH19.5/SOL19.5/NEAR17.5/ZEC17.5% | 40%; same weights | BTC/ETH/SOL20, NEAR/ZEC25 in both sleeves |
| 2 | 60%; SOL40/NEAR30/ZEC30% | 40%; same weights | 20 throughout |
| 3 | 100%; SOL40/NEAR30/ZEC30% | Disabled | 20 |
| 4 | 60%; BTC/ETH1:1 | 40%; SOL/NEAR/ZEC1:1:1 | Spot20, Short25 |
| 5 | 60%; SOL/NEAR/ZEC1:1:1 | 40%; BTC/ETH1:1 | Spot25, Short20 |

Setup 1's 650/350 USDT denotes initial allocation to the two coin groups. The
user explicitly retained shared reinvestment within each Spot/Short sleeve,
rather than separate group capital books. Setup 3 inherits SOL/ZEC/NEAR4:3:3
from setup 2. Equal thirds use 28-digit Decimal precision with a final-symbol
residual of at most 1e-28 so weights sum exactly to one.

All other rules remain those in [the five-coin study](donchian-adx-five.md):
Donchian30/10 H4, EMA200/50, VolumeMA20×1.2, approximate prior20-day M15 volume
profile, ATR14×3 stops/trailing and ATR sizing, ADX strictly above its threshold
and rising, DMI in the entry direction, realized-only sleeve reinvestment,
Short-only1x, combined UTC daily loss3% with next-day-and-flat resume, and
observe-only drawdown. Existing positions are never rebalanced.

The original five-coin manifest and approved exceptions are reused unchanged.
All five coins are validated and prepared to retain the same source checksum;
only allocated market/coin pairs participate in fills, positions and valuation.
The Spot-only setup has zero Perp notional, margin and funding. Original three-
and five-coin profiles remain available unchanged.

```bash
uv run python scripts/replay_donchian_adx.py \
  --universe setups \
  --inputs /home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-five-20261005/approved-data/inputs.json \
  --report-root /home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-setups-20261005/study-01
```

Commit the clean implementation before running. The runner binds commit, source
hashes, config hashes and input SHA256 before replay, then mechanically repeats
each case after serialization. Summary, result ID, methodology and all three
full journals must match. Resume rejects changed source/config/input bindings.

Generate the verified analysis with
`intraday.replay_v2.donchian_setups_analysis.describe(comparison_path, output_root)`.
It provides capital/return/DD/cost/daily-stop tables; wins/losses after costs;
coin and coin×market contributions; yearly/half-year marked equity; DD recovery;
and a CSV containing every executed trade from the five setups. No new ADX-off
counterfactual is claimed for these different allocations.

Private delivery root:
`/home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-setups-20261005`.

## Historical results

Frozen source commit: `0ca9bc9d8e24b7d82454d08df3ec049127980e07`.
Original approved manifest SHA256:
`fe09301c47264605e6fb2a0a2848f15ef5063ff9157f685d29a247358743b8a9`.
Each new setup completed its serialized mechanical repeat with identical full
journals, summary, methodology and result ID.

| Setup | Final USDT | Net return | Max DD | Trades | Wins | Losses | Daily stops |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1564.75 | 56.48% | 9.48% | 651 | 253 | 398 | 0 |
| 2 | 1663.98 | 66.40% | 10.85% | 419 | 177 | 242 | 0 |
| 3 | 1669.19 | 66.92% | 19.45% | 218 | 84 | 134 | 14 |
| 4 | 1497.05 | 49.71% | 12.45% | 315 | 130 | 185 | 0 |
| 5 | 1468.59 | 46.86% | 21.37% | 315 | 111 | 204 | 1 |

Setup3 earns only5.21USDT more than setup2 with materially higher DD and more
daily stops. Setup1 has the lowest DD among the five new setups. Setup4 had not
recovered the peak of its worst DD episode by the end of the study; setup5 took
1327.91observed days from that episode's peak to recovery. These are distinct
allocation/universe/ADX combinations, not isolated tests of an ADX threshold.
All five new returns are below the prior five-coin ADX20 return70.88%.

Private artifacts: `study-01/runs/comparison.json`, `analysis-01/report.md`,
`analysis-01/analysis.json` and `analysis-01/trade-samples.csv`. The CSV contains
all1918executed trades, including precise prices, costs and net PnL. Every real
entry was separately checked against its mapped ADX threshold, rising ADX,
directional DMI and retained EMA/volume/profile filters in `entry-verification.json`.
Year/half-year analysis uses marked equity; trade outcomes use closed net PnL.

Regression1352pass, one existing Hermes test failure due to the absent ignored
`.env.template`; wheel/sdist build passes. The final delivery includes the full
legacy verification receipts, source/accounting checks and figure in private
`final-report.md`, `final-verification.json` and `final-manifest.json`.
To resume an existing run, restore its frozen source commit; a changed HEAD or
source/config/input binding is rejected. A docs-only delivery commit does not
replace the engine commit recorded in the original binding.
