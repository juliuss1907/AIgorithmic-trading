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

No completed five-coin comparison exists yet. ADX20 remains a preferred research
candidate. This study uses already-seen historical data and grants no OOS,
trading, activation or deployment approval. Only local commits are permitted.

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
