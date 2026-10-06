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
