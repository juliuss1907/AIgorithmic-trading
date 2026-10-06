# Setup 2 isolated coin accounts

The user requested SOL, ZEC and NEAR separately and confirmed **1,000 USDT per
account**. Three independent accounts therefore represent 3,000 USDT of initial
capital. They must not be presented as a combined 1,000 USDT portfolio.

Opt-in `--universe single` locks exactly three cases, one symbol in each account's
Spot and Perp weights. Every account uses Spot600/Short400, ADX strictly >20 and
rising, directional DMI, and the unchanged [setup 2 rules](donchian-adx-setups.md).
Realized reinvestment remains separate within Spot/Short. Daily loss3% applies
to each account; resume requires next UTC day and flat. DD is observe-only.
Window `[2022-01-01, 2026-10-02)` UTC; no resets, cross-account transfers, new
thresholds or limit-order fill assumptions.

The approved five-coin manifest, original rows and per-series exceptions are
reused unchanged. All five coins are audited/prepared once for the three cases,
preserving the dataset checksum. Only the selected coin trades, pays funding
or participates in the account's valuation.

```bash
uv run python scripts/replay_donchian_adx.py \
  --universe single \
  --inputs /home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-five-20261005/approved-data/inputs.json \
  --report-root /home/julius/julius-workspace/ai-labs/projects/system-trading/state/research-reports/donchian-adx-single-20261006/study-01
```

Before replay, commit the clean implementation. The runner binds source commit,
runtime files, configurations and dataset sources, then repeats each serialized
case and compares result ID, summary, methodology and all three full journals.
Resume rejects changed bindings. Defaults and old profile serialization remain
unchanged.

Use `donchian_adx_study.verify_three_coin(comparison_path, output_root,
universe='setups')` to reverify only the original Setup-2 publication with the
same full-journal checks. Generate analysis with
`donchian_single_analysis.describe(comparison_path, output_root,
baseline_path=old_setups_comparison_path)`.

The analysis reports return, DD/recovery, wins/losses, profit factor, costs,
daily stops, holding time and largest winner/loser; Spot/Short and calendar
year/half-year breakdowns; and every executed trade in CSV. It compares old
coin contributions against their initial400/300/300 allocations. This ratio is
descriptive attribution, not the return of an independently compounded account.
Isolated reinvestment and daily guards may change subsequent trades and sizing.

Research only on already-seen historical data. No push, merge, deployment, LLM
calls or trading activation. The managed filesystem became read-only for the
previous research worktree/private-report directory during implementation;
execution uses a separate local checkout under `/tmp` and persistent private
artifacts under the gitignored workspace `state/research-reports/` directory.

## Verified historical results

Frozen engine: `c772c53887972ca16344481e6283514731014bf8`.
Manifest SHA256: `fe09301c47264605e6fb2a0a2848f15ef5063ff9157f685d29a247358743b8a9`.
Prepared dataset: `c9c42598a2a07d675f5366e692a399ebee621abf85b64e65faeb89850c5b1665`.
All three cases reproduce their entire journals after serialization; the old
Setup-2 publication also matches exactly. All15older config IDs are unchanged.

| Account | Final USDT | Net USDT | Return | Max DD | Trades | Wins/losses | Daily stops |
|---|---:|---:|---:|---:|---:|---:|---:|
| SOL | 1546.70 | 546.70 | 54.67% | 21.70% | 149 | 60/89 | 13 |
| ZEC | 1182.28 | 182.28 | 18.23% | 22.21% | 135 | 55/80 | 10 |
| NEAR | 1974.86 | 974.86 | 97.49% | 20.68% | 137 | 64/73 | 4 |

| Account | Spot net | Short net | Fees | Slippage | Funding paid | Mean net/trade | Mean hold hours |
|---|---:|---:|---:|---:|---:|---:|---:|
| SOL | 436.53 | 110.18 | 133.50 | 78.01 | 19.26 | 3.67 | 62.42 |
| ZEC | -19.90 | 202.18 | 66.83 | 47.10 | -10.36 | 1.35 | 62.41 |
| NEAR | 551.66 | 423.20 | 98.74 | 67.40 | 6.21 | 7.12 | 74.92 |

Negative funding paid is a credit. NEAR has the greatest historical net return;
ZEC's Spot sleeve loses money while its Short sleeve earns the account profit.
Every standalone DD exceeds the original Setup-2 portfolio DD10.85%; individual
daily stops are13/10/4 versus0in the shared portfolio.

Original coin contributions divided by initial400/300/300USDT allocations are
63.41%/35.42%/101.36% for SOL/ZEC/NEAR. These are allocation attribution ratios,
not isolated account returns. All three independent returns are lower than
those ratios; shared reinvestment and daily guards change subsequent fills,
exit times and sizes. This study does not separate those effects.

Private evidence: `study-01/runs/comparison.json`, `analysis-01/report.md`,
`analysis-01/analysis.json`, `analysis-01/trade-samples.csv` (421trades),
`setup-two-verification/verification.json`, `final-verification.json`,
`final-report.md` and `equity-drawdown.png`. All421entries pass the retained
ADX/DMI/EMA/volume/profile filters; trade/cost/capital, daily-stop, full-curve DD
and calendar-period accounting reconcile independently.

Regression1287pass,1known missing Hermes template failure. Full-suite execution
hangs in TestClient, also reproduced with an empty FastAPI app;83tests across12
web files remain unverified. Diagnostics and exclusions are preserved. Build
wheel/sdist passes. The original user checkout tracked state is preserved;
the code is in local `state/research-checkouts/donchian-adx-single-20261006/`
and the frozen engine is also saved as `engine.bundle` in the report root.
