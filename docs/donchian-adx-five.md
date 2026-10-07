# Five-coin A4 ADX sensitivity

Local historical research on BTC, ETH, SOL, NEAR and ZEC. The locked five cases
disable ADX/DMI or require Wilder14 ADX strictly above20/18/15/25, rising ADX
and directional DMI. All other A4 Donchian30/10 rules and execution costs remain
the same as [the three-coin study](donchian-adx-study.md).

The continuous window is [2022-01-01,2026-10-02) UTC with1000USDT initial
capital, Spot60%/Short40%, Short-only1x and independent realized-only reinvestment.
Each sleeve allocates BTC40%, ETH20%, SOL20%, NEAR10%, ZEC10%. No resets,
separate coin experiments or extra thresholds are added. Daily portfolio loss3%
resumes next UTC day when flat; drawdown is descriptive, with no terminal halt.

## Inputs and compatibility

The default runner remains the three-coin profile. `--universe five` requires a
`donchian-five-manifest-1` input manifest. It binds the unchanged joined three-coin
export plus six snapshot references per added coin (Spot/Perp H4 and M15, native
mark M15, funding), source SHA256 and snapshot IDs. Individual bounded reads
avoid a combined five-coin JSON export. Candle warmup remains600H4 and1936M15;
mark coverage starts at the active window as in the original pipeline.

Sparse Spot/mark validation and profile coverage adjustments apply per series.
NEAR/ZEC reject the original sparse-bar and native-boundary whitelists. New bars
pass calendar/duplicate, timestamp, finite/OHLCV and exact active H4/M15 checks,
except the individually approved, separately checksum-bound source tuples.
Zero volume is reported; zero-volume profiles block entry.
Funding rates/times and raw rows are preserved. A missing funding price can use
an actual native mark M15 open0–31ms before settlement, with bound row provenance;
missing mark evidence never authorizes an estimated price.

## Commands

Collect native sources and validate before replay:

```bash
uv run python -m intraday.replay_v2.donchian_five_data \
  --base-inputs /path/to/three-coin/inputs.json \
  --output-root /path/to/new-private-data
```

`--resume` revalidates existing immutable checkpoints and the base/config binding.
The collector retains a rejected original API page and a blocked receipt when
strict QA fails. `audit_raw_series(symbol, tag, root)` acquires the full original
series for review only and records all missing opens and noncanonical close times;
these exports have `accepted_snapshot=false` and cannot be replay inputs.

Once source QA is approved and complete:

```bash
uv run python -m intraday.replay_v2.donchian_five_data \
  --approved-sources /path/to/source-approval.json \
  --base-inputs /path/to/three-coin/inputs.json \
  --output-root /path/to/new-private-approved-data

uv run python scripts/replay_donchian_adx.py \
  --universe five --inputs /path/to/data/inputs.json \
  --report-root /path/to/new-private-study
```

The runner writes a binding of commit, source/config checksums and input SHA256
before feature preparation or new results. Each case mechanically reproduces its
summary, methodology and all equity/trade/event journals. `--resume` refuses any
changed binding or case identity. Reports identify all five coins and weights.

`donchian_adx_analysis.describe(comparison_path, output_root, baseline_path=...)`
exports yearly/half-year metrics, all ten coin×market contributions, NEAR/ZEC net
contributions, fees/slippage/funding/daily stops, worst-DD recovery and conditional
ADX-off blocked-winner/loser attribution. The baseline must be the same-window,
locked three-coin five-case study. ETH/SOL weights decrease30%→20%, so differences
cannot be attributed entirely to adding NEAR/ZEC. Attribution is conditional on
executed ADX-off entries and sizing, not an exact counterfactual portfolio PnL.

`donchian_adx_study.verify_three_coin(comparison_path, output_root)` replays all
five old cases and compares their config/result IDs, summary, methodology and
every complete journal against existing immutable publications.

## Current acquisition status

The first collection stopped before any new strategy replay. NEAR/ZEC native
Perp H4/M15 are retained, alongside original funding rows. Both new mark series
have a missing M15 open at2023-11-10T03:45UTC. New Spot metadata/availability
anomalies also need individual approval. Full original-series auditing and
verification are recorded in the private root:

`~/.local/state/aigorithmic-trading/reports/donchian-adx-five-20261005/`.

The exact new source exceptions were subsequently approved by the user and all
five cases have completed with full-journal mechanical verification. ADX20 remains
a preferred research candidate. This study uses already-seen historical data and
grants no OOS, trading, activation or deployment approval. Only local commits
were created.

## Verified implementation

At commit `1e387e3`, all five original three-coin cases matched config/result IDs,
summary, methodology and every equity/trade/event journal against their existing
publications. The new profile has config roundtrip, locked weights, case resume,
manifest tamper rejection, mixed sparse/dense-series and new-coin whitelist tests.

Full regression:1332 passed, one existing Hermes bundle test failed because the
ignored `.env.template` is absent in this worktree. Wheel/sdist build succeeded.
Private logs and the verification receipt are in the new report root.

The completed raw audit identifies2H4 closeTime anomalies,3partial Spot M15
closes,5missing Spot M15 bars per added coin,1missing mark M15 bar per coin and
5exact H4/M15 price differences. All observed OHLCV are finite and valid; no
duplicate or unexpected opens occur. Funding has5205settlements per coin and
2005missing-price references per coin, all backed by actual native opens0–31ms
before settlement, with no funding gap. `data-approval.md` gives the exact
symbol/time/price tuples and proposed treatment; `data-audit.json` binds all raw
files and reference rows. The user subsequently approved the exact listed
exceptions; `source-approval.json` binds the reply, full source audit and proposal.
The separately scoped policy has passed strict coverage, OHLCV, exact price-tuple
and funding validation. New historical runs use `approved-data/inputs.json`.

## Completed five-coin results

Frozen engine commit `1c6bcab`; input manifest SHA256
`fe09301c47264605e6fb2a0a2848f15ef5063ff9157f685d29a247358743b8a9`.
All five cases completed and reproduced summary, methodology and full journals
on their mechanical repeats. No state reset occurred within the1735-day window.

| Case | Final USDT | Net return | DD | Trades | Fees | Slippage | Funding paid | Daily stops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ADX/DMI off | 1665.51 | 66.55% | 10.01% | 831 | 158.65 | 97.90 | -1.75 | 1 |
| ADX20 | 1708.80 | 70.88% | 11.46% | 696 | 134.66 | 82.62 | 0.28 | 0 |
| ADX18 | 1680.12 | 68.01% | 10.20% | 743 | 142.37 | 87.53 | -0.49 | 0 |
| ADX15 | 1611.74 | 61.17% | 10.31% | 795 | 149.81 | 92.43 | -1.11 | 1 |
| ADX25 | 1506.05 | 50.61% | 9.35% | 570 | 98.55 | 60.06 | 1.82 | 0 |

Funding paid is signed; negative means net receipt. Worst-DD episodes recovered
after193.21/256.04/256.02/196.36/177.61observed days, respectively. Full UTC peak,
trough and recovery timestamps are in the reports; these are not the duration
of every drawdown episode.

| Case | NEAR net USDT | ZEC net USDT | Final delta vs3coin USDT |
|---|---:|---:|---:|
| ADX/DMI off | 87.88 | 70.75 | +3.17 |
| ADX20 | 109.57 | 45.02 | -0.29 |
| ADX18 | 94.28 | 61.92 | -11.79 |
| ADX15 | 89.11 | 52.67 | -12.47 |
| ADX25 | 64.81 | 22.62 | -55.24 |

NEAR/ZEC add positive net PnL in every case. The total portfolio delta also
reflects reduced ETH/SOL allocations and altered subsequent sizing/positions;
positive added-coin contributions do not guarantee higher total capital.
ADX20 has both the highest return and highest measured DD in this matrix.
Its capital is nearly equal to the original3coin case1709.09USDT, while observed
DD decreases13.20%→11.46%. This is sensitivity evidence, not automatic acceptance.

On ADX-off executed entries, ADX20 would reject247trades:94winners net+902.96USDT
and153losers net−622.92USDT. The rejected set is net-positive+280.04USDT, yet
the actual ADX20 portfolio finishes43.30USDT above ADX-off. This illustrates why
blocked-trade PnL is not an exact counterfactual: capital, entry timing and later
positions change. Full threshold attribution and its limits are preserved.

Authoritative private outputs under the new root are `final-report.md`,
`analysis-01/report.md`, `study-01/runs/comparison.json`, `equity-drawdown.png` and
`final-verification.json`. Annual and calendar-half-year marked PnL both reconcile
to total net PnL, and all ten coin/market cost contributions reconcile to capital.
All five original3coin cases matched again after the final policy, including
config/result IDs, summary, methodology and every journal.

Final full regression:1343passed, the same one unrelated Hermes missing-template
failure; wheel/sdist build passed. No push, merge, deployment, model call or
trading activation occurred.
