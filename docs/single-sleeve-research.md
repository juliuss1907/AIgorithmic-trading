# BTC/ETH/SOL full-capital research

Completed local experiment, 2026-10-04; no runtime/provider/worker/gate changes,
broker orders, activation, push or deployment. Initial capital1,000USDT per case;
703 days from 2024-10-29 00:00 UTC to 2026-10-02 00:00 UTC (07:00 UTC+7).
BTC/ETH/SOL weights50/25/25 in both cases; no NEAR/ZEC or reserve/subsidy.

## Locked setup

- Spot-only: 100% own realized capital budget, H4 Donchian30/8, closed D1 EMA50
  trend filter, ATR14 sizing, 10% emergency price stop; no trailing.
- Perp-only: 100% own realized **collateral** budget, isolated3x, both long and
  short; closed H1 Donchian30/8, closed H4+H8 EMA50 direction/slope consensus,
  ATR14 H1 x3 stop fixed at entry, native M15 contract stop detection.
- Perp trailing retains the existing rule: projected net PnL divided by entry
  **notional**, including entry/assumed exit costs and accrued funding; arm3%,
  floor=peak minus3 percentage points. Observe M15 close, latch breaches, fill
  next M15 open; open gaps may also trigger. No fixed daily profit target.
- Daily loss3%; Perp own marked equity sampled M15 plus actual funding/cost
  events. Parent remains H4/funding/cost sampled, Spot remains H4. UTC daily
  resumption needs next day, flat positions and permitted risk state.
- DD10% is observe-only, not a terminal stop. Gap/next-open costs may overshoot
  daily thresholds. Existing collateral-exhaustion safeguards remain.
- Reinvest **realized** net PnL in the same sleeve; unrealized PnL changes risk,
  not new sizing. 100% is an available budget, not forced continuous full
  exposure: signal filters, ATR sizing, fees, cash and locked margin limit entries.

Perp notional can approach3,000 initially, rather than a1,000 notional budget
requiring only333 margin. Each request is realized collateral×coin weight×3,
trimmed by remaining locked-margin room and free cash including entry fees.
Spot fills also include entry costs within available cash.

## Verified results

Net results after modeled costs: Spot10bps fee/5bps slippage and Perp5bps fee/5bps
slippage per fill, plus complete historical funding. Both run the full window.
Portfolio DD uses a passive common-M15 audit with Spot H4 as-of valuation.

| Case | Final USDT | Net PnL | Return | Portfolio DD | Closed trades |
|---|---:|---:|---:|---:|---:|
| Spot Donchian100 | 1,485.18 | +485.18 | +48.52% | 16.49% | 93 |
| Perp intraday3x trailM15 full margin | 494.28 | -505.72 | -50.57% | 69.84% | 772 |

Spot contributions: BTC+193.25, ETH+95.86, SOL+196.07. Perp: BTC-135.72,
ETH-1.37, SOL-368.63. Perp gross price PnL+570.11 less exchange fees517.54,
slippage517.54 and funding40.76 gives net-505.72. Long395 trades net-190.25;
short377 net-315.47. There are100 trailing exits,86 stop exits,117 Perp daily
halts and138 parent daily pauses. No DD terminal halt.

Both exceed the descriptive10% DD research criterion. These are not official
gate evaluations or trading recommendations. Different markets, directions,
clocks, leverage and fees make this more than a single-variable comparison.
Neither independent holdout nor exchange filter/partial-fill/liquidation parity
is established. Common-grid DD is not exact intrabar DD.

No reserve means accrued funding can reduce free cash below zero while fixed
entry margin is committed (observed minimum about-1.76USDT). Entry constraints
still use available cash and block exhausted room; this model behavior is not a
guarantee that an exchange would support the account unchanged.

## Reproduce offline

```bash
uv run python -m intraday.replay_v2.single_sleeve_study \
  --frozen-root /path/to/perp-short-reserve-20261004-703days \
  --sol-inputs /path/to/single-sleeve-20261004-sol-inputs \
  --report-root /path/to/new-private-directory
```

The replay does not collect data. SOL was explicitly acquired separately using
the existing public Futures candle/funding collectors, then frozen with source,
fetch time, raw rows, snapshot hashes, full funding coverage and strict native
H1/H4/H8/M15 boundary validation. BTC/ETH retain their original snapshots;
three-coin Spot is an unchanged-row subset of the original five-coin export.
New combined bundles record parent file hashes; original exports/source database
are never overwritten. Existing report directories are refused.

Reports:
`/home/julius/.local/state/aigorithmic-trading/reports/single-sleeve-20261004-btc-eth-sol-703days/`.
Read `comparison.md`/`comparison.json`; individual manifests, summaries and full
trade/event/equity journals are under `reports/`. Sidecars contain fine Perp
risk observations and passive common-grid audits. All results/series are replayed
twice offline and verified at publication; source and lineage hashes remain unchanged.
`legacy-default-verification.json` verifies the previous C intraday identity,
full summary and three journals are unchanged by the opt-in book hook.
Regression: 1,214 tests passed; package build passed.


## Rerun after daily-loss fixes — 2026-10-06

Two ledger fixes change the evaluator: a parent daily-loss resume no longer
erases a loss taken on the new UTC day before the book is flat, and the parent
and Perp-sleeve UTC daily baselines now anchor at the 00:00 marks in every
engine (previously only the Donchian filter book did). The study was replayed
offline from the same frozen inputs (identical SHA256) into a new directory;
every variant passed the deterministic double replay. Old evidence is kept.

Evaluator `historical-single-sleeve-research-v1.1`; report:
`/home/julius/.local/state/aigorithmic-trading/reports/single-sleeve-20261006-rerun/`.
Spot Donchian100 is unchanged. Perp-only changes slightly (figures above updated):
its first behavioral divergence is 2024-12-03 around 15:00 UTC, with the Perp
daily return at the −3% threshold, after which fills drift (770→772 trades).
Trailing exits100, stop exits86, Perp daily halts117 and parent pauses138 are
unchanged. Full `uv run pytest -q`: **1,245 passed**.
