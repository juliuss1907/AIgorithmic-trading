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
