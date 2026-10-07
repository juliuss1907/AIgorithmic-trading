# A4 ADX sensitivity research

Accepted study: continuous account over `[2022-01-01, 2026-10-02)` UTC,
initial1000USDT, five cases: ADX/DMI off, ADX>20, >18, >15 and >25 control.
Enabled cases require ADX rising and correct DMI direction. Other A4 filters,
signals, fills, costs, stops and sizing are unchanged. This is already-seen
historical research, not a new OOS acceptance gate.

## Run

```bash
uv run python scripts/replay_donchian_adx.py \
  --inputs /absolute/path/to/joined/inputs.json \
  --report-root /absolute/path/to/new-study
```

Alternatively supply `--early-inputs` and `--late-inputs` in place of `--inputs`.
The collector validates parent bundles, overlapping native rows and approved
source policies. It fetches only16 real mark M15 bars per coin at the4-hour
join. It never replaces source prices or creates synthetic bars.

Existing output directories fail closed. `--resume` reuses verified checkpoints
only with exactly the same source commit/hashes, input checksum and configs.
Do not edit source or switch commits mid-run. The300MB joined-input reader is
scoped to this study; existing readers retain their200MB limit.

The runner binds sources/configs/inputs before simulation, prepares shared
causal features, mechanically reruns each case, and compares summaries,
methodology and all full journals. An atomic report manifest binds each
summary, trade log, equity curve and event journal.

## Describe

```python
from intraday.replay_v2.donchian_adx_analysis import describe
describe('/absolute/path/to/new-study/runs/comparison.json',
         '/absolute/path/to/new-analysis')
```

Outputs: `report.md`, `analysis.json`, `manifest.json`. They include overall
returns/DD, coin/market contributions, calendar half-years, annual marked
equity, worst drawdown episode and rejected winning/losing entries.

Rejected-trade analysis uses actual ADX-off entries and their original size.
It measures which historical winners/losers fail each filter, **not** exact
counterfactual money earned/avoided: capital, timing and later entries differ
between complete strategy runs. Read both attribution and whole-account
results.

## Fixed execution and risk

- Spot60% / short-only Perp40%; BTC40%/ETH30%/SOL30% in each sleeve, Short1x.
- Own realized-only reinvestment; no capital transfer or rescue reserve.
- Donchian30/10 H4; EMA200/50 H4; preceding VolumeMA20>1.2x; prior20-day
  approximate M15 volume profile (uniform candle volume,50bins,VA70%).
- Simple TR mean14 preserves legacy ATR sizing `min(1, .02 / ATR%)`.
  ATR14x3 initial/H4 ratcheting price stop, checked on native M15.
- Donchian exit retained; no fixed daily profit target.
- Combined marked UTC daily loss3%; flatten at next available native M15 open,
  resume next UTC day only when flat. DD observe-only, not terminal halt.
- Fee/slippage each fill: Spot10/5bps, Perp5/5bps; historical funding separate.

Approved disclosures remain explicit: five unavailable Spot M15 bars, one
stale mark M15 interval, missing historical funding settlement price quotes
referenced to actual mark opens0–31ms earlier, and eight exact H4/M15 price
view disagreements (five prior plus three at the2024 join). Raw sources
remain unchanged. A partial source row's actual closeTime is retained.

DD is sampled at known observations. Daily3% is a trigger, not a guaranteed
loss ceiling. There is no exact order-book/partial-fill/tick liquidation model.
The study does not call models, mutate runtime DB/gates/champions, activate
trading, place orders or deploy anything.

## Completed continuous study (2026-10-05)

Frozen source commit: `56eea87`. Five cases complete; all summaries/methodology
and full journals reproduce mechanically. Inputs/source files unchanged.
Source/data/config checksums are in the run binding and comparison receipt.

| ADX | Final USDT | Net USDT | Return % | DD % | Trades |
|---|---:|---:|---:|---:|---:|
| Off (DMI off) |1662.34|662.34|66.23|14.24|505|
|20|1709.09|709.09|70.91|13.20|425|
|18|1691.91|691.91|69.19|12.60|456|
|15|1624.22|624.22|62.42|14.75|487|
|25 control|1561.29|561.29|56.13|10.47|344|

ADX20 has the highest whole-account net; ADX25 the lowest observed DD. This
does not establish a future winner. ADX20's worst drawdown episode remained
underwater about437days, compared with about197days for ADX18; DD amplitude
and recovery time measure different risks.

Conditional ADX-off attribution: ADX25 rejects104 winners totaling1659.17USDT
net and179 losers totaling1232.93USDT loss. ADX20 rejects56 winners991.83USDT
and91 losers655.50USDT. These are NOT direct whole-account net differences:
ADX20 actually earns46.76USDT more than off because subsequent timing, entry
opportunities and capital paths differ. Filters do not merely delete fixed
trades from the same unchanged portfolio.

Private report root:
`/home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-2022-2026/`.

- Inputs/QA: `data-20261005-01/`.
- Frozen binding/checkpoints/comparison/full journals: `study-20261005-02/runs/`.
- Full report/analysis/manifest: `analysis-20261005-01/`.
- Compatibility proof: `legacy-verification-20261005-01.json`.
- Retained infrastructure attempt: `study-20261005-01/`.
- Retry disclosure and byte-identical journals proof:
  `attempt-disclosure-20261005-01.json`, `retry-journal-verification-20261005-01.json`.

Full suite1320passed; one existing Hermes distribution test fails because
ignored `.env.template` is absent in the clean worktree. Wheel/sdist build
passes. Six prior legacy cases preserve exact result IDs/summaries and all
journals; the original OOS engine/criteria seal remains intact. No push,
merge, VPS change, model call or trading activation was performed.
