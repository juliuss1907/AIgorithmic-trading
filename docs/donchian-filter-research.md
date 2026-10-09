# Donchian Spot + Short1x filter research

Opt-in local research, not a runtime rule, official gate, Binance Demo simulation,
deployment or activation. Four Donchian variants use one portfolio and progressively
added filter groups, so improvements and lost opportunities remain inspectable.
Existing studies and source snapshots are not replaced.

## Locked setup

- Initial1,000USDT, Spot60% / isolated Short1x40%; no reserve, subsidy or transfer.
  Each sleeve uses BTC40% / ETH30% / SOL30%. No Perp longs or Coin-M collateral.
- Independent realized-only reinvestment within each sleeve. Unrealized gains
  affect equity/risk, never new sizing. Weights govern new entry requests; no
  automatic rebalancing or forced continuous investment.
- H4 Donchian20/8,20/10,30/8,30/10. Entry channels exclude the trigger candle;
  Spot close above upper band, Short close below lower band. Fill next H4 open.
- Retain legacy ATR14 simple mean True Range sizing:
  `min(1, .02/(ATR/close))` × own realized sleeve budget × coin weight.
  Each sleeve must fund its own fill costs; already-invested principal cannot
  be reused as cash. Fractional fills omit historical exchange lot filters.
- Retain exit8/10: Spot closes below lower exit channel, Short above upper exit
  channel. Exit fills next H4 open, independent of entry filters.
- Both initial stops and trailing use ATR14×3. At entry initialize from fill;
  at each closed H4 ratchet Spot stop to highest high since entry−3ATR, Short
  stop to lowest low+3ATR. Stop never loosens. No fixed emergency10% stop,
  fixed profit target, or old net-profit arm3% trailing is added on top.
- Native M15 checks the old stop before H4 updates. A newly tightened H4 stop
  becomes active at the next M15 open, never retroactively in the closed bar.
  Gap fills use the unfavorable open. Touch fills use the old stop price and
  are logged at M15 close; exact intrabar time/path is unknown. No same-bar reentry.
- Parent daily stop3% uses **combined marked equity at UTC midnight opening
  prices**, before midnight funding/fill costs. This is not3% of each sleeve.
  Close/funding breaches latch until next available M15 open; open/cost breaches
  can flatten at that open. Both markets close and pending entries are cancelled.
  Resume requires next UTC day and flat positions. Overnight gaps still trigger
  price stops, even when the new daily baseline differs from the prior close.
- Drawdown10% is descriptive/observe-only, not terminal halt. Gap and exit costs
  can overshoot daily3%. Existing unsupported collateral-exhaustion handling is
  retained; no precise exchange liquidation model is claimed.
- Same assumed per-fill costs as prior research: Spot10bps fee+5bps cash-charged
  slippage; Perp5bps fee+5bps slippage. Actual historical funding is separate.
  These are retained research assumptions, not a new verified account fee quote.

## Twenty-case matrix

Each level runs all four Donchian pairs with identical sizing, exits, clocks and costs.

| Level | Cumulative entry filters |
|---|---|
| A0 | Donchian only |
| A1 | A0 +H4 EMA200 and EMA50 |
| A2 | A1 +ADX/DMI14 |
| A3 | A2 +Volume MA20 |
| A4 | A3 +Volume Profile |

EMA200: Spot close above / Short below. EMA50: close on the same side and EMA50
moving in that direction versus prior H4. EMA seeds use first50/200 closes.
ADX/DMI use Wilder14 smoothing: ADX strictly>25 and above prior H4; +DI>−DI for
Spot, −DI>+DI for Short. This is strength/direction confirmation, not proof a
breakout will succeed. Volume strictly>1.2×SMA20 of prior candles; the reference
excludes trigger volume. Filters never prevent risk exits.

Volume Profile uses the120 H4 periods **before the trigger H4** (20days), from
native M15 of the matching market/symbol.50 equal price bins; each M15 volume is
distributed uniformly across low/high (zero range goes in one bin). POC highest
volume, ties lower; contiguous70% Value Area expands toward highest-volume
adjacent row, ties upper. Floating ties use relative tolerance1e-12. Spot must
close above VAH, Short below VAL. It is approximate candle-derived volume-at-price,
not observed individual trades, buy/sell flow or guaranteed support/resistance.
Zero-volume profile blocks A4; it is not silently bypassed.

## Data and publication

Active window:2024-10-29 00:00UTC to2026-10-02 00:00UTC,703days
(07:00UTC+7 endpoints). H4 warmup600 bars; M15 warmup484hours supplies the
full20-day profile preceding the first trigger candle. H4 signals remain UTC.
`comparison.md` and existing dashboard history rendering use UTC+7; raw journals
retain canonical UTC timestamps.

Reuse unchanged BTC/ETH/SOL SpotH4, PerpH4/M15/markM15 and funding from frozen
short-reserve/SOL studies. Explicitly collect public SpotM15 and missing prefixes
only. Full original Binance row columns are retained. Source/lineage hashes,
native calendar/OHLC/finite values and active H4/M15 boundary prices are checked.
Warmup periods are validated as native bars but active-window boundary checks
do not reinterpret the previously documented2024-10-28 cross-timeframe discrepancy.
No database migration, credential, model call or order is needed.

Collection and replay are separate. Replay reads frozen exports and publishes
private manifests plus full trade/event/equity journals. Each summary and all
three journals reproduce on a second offline replay. Original source and new
input files are hash-checked again before final comparison publication. Existing
report roots are refused. A root without `comparison.json` is incomplete.

## Commands

Explicit public acquisition, no API keys:

```bash
uv run python -m intraday.replay_v2.donchian_filter_data \
  --frozen-root /path/to/perp-short-reserve-20261004-703days \
  --sol-inputs /path/to/single-sleeve-20261004-sol-inputs \
  --output-root /path/to/new-input-directory
```

`--resume` reuses validated checkpoint files in an incomplete collection root;
`--reuse-root /path/to/prior-input-directory` reuses prior supplemental snapshots
and fetches only missing prefixes into a new root. Neither overwrites old evidence.

Offline20-case replay:

```bash
uv run python -m intraday.replay_v2.donchian_filter_study \
  --inputs /path/to/input-directory/inputs.json \
  --report-root /path/to/new-report-directory
```

Verified local frozen inputs:
`/home/julius/.local/state/aigorithmic-trading/reports/donchian-filters-20261004-inputs-complete/inputs.json`.
Batch report directory:
`/home/julius/.local/state/aigorithmic-trading/reports/donchian-filters-20261004-703days/`.

Compare final capital, net return, M15 marked DD, trades, daily stops, holding times,
per-coin/market contributions, gross/net and each cost component. Reports include
all filter rejection reasons and continuous six-month segment metrics without
restarting positions/capital at segment boundaries. Counts can overlap when one
signal fails multiple filters. Old research windows have already been viewed:
none is a new untouched holdout, and positive return is not automatic acceptance.

## Verified703-day results — 2026-10-04

All20 cases completed, each full journal reproduced on a second offline replay,
and input/source hashes remained unchanged. Best return **within each level**:

| Level / Donchian | Final USDT | Net return | M15 marked DD | Trades |
|---|---:|---:|---:|---:|
| A0 /30/10 | 1,072.78 | +7.28% | 23.80% | 325 |
| A1 /30/10 | 1,202.73 | +20.27% | 20.86% | 269 |
| A2 /30/10 | 1,134.42 | +13.44% | 10.64% | 164 |
| A3 /30/10 | 1,148.59 | +14.86% | 9.93% | 156 |
| A4 /30/10 | 1,159.11 | +15.91% | 8.46% | 140 |

All four A4 cases have measured DD below10%, returns+11.80% to+15.91%, and
140–147 closed trades. A4 /30/8 has the lowest measured DD8.38%; A4 /30/10
has the highest return among the cases below10% DD. These descriptive thresholds
are not an official gate result or a new independently validated champion.

For A4 /30/10, Spot net+88.51USDT and Short net+70.60; final own capital688.51
and470.60. Contributions by coin (Spot / Short): BTC+24.07 /+7.57,
ETH+60.67 /+46.85, SOL+3.77 /+16.18. Fees38.86, slippage23.45, funding−0.16
(net receipt).140 trades:117 ATR stop/trailing touches,2 stop gaps,21 Donchian
exits. No daily3% stop was hit in this case; the mechanism is covered by synthetic
gap/close/funding risk checks and regression tests, not inferred from zero pauses.

Continuous segment returns are+3.49%,+7.74%,+2.69%,+1.24% (first segment is
shorter than6months). They carry prior capital and positions, so are not independent
flat-start experiments. Full20-row table is in `comparison.md`; detailed cost,
filter, trade and segment data are in `comparison.json` and individual reports.

Verification:1,240 tests passed with29 existing dependency deprecation warnings;
package build and CLI help checks passed. Independent correctness/data reviews
had no unresolved Required/Critical findings. No push, merge, deployment or
activation was performed.


## Rerun after daily-loss fixes — 2026-10-06

Two ledger fixes change the evaluator: a parent daily-loss resume no longer
erases a loss taken on the new UTC day before the book is flat, and the parent
and Perp-sleeve UTC daily baselines now anchor at the 00:00 marks in every
engine (previously only the Donchian filter book did). The study was replayed
offline from the same frozen inputs (identical SHA256) into a new directory;
every variant passed the deterministic double replay. Old evidence is kept.

Evaluator `historical-donchian-filter-study-v1.1`; report:
`/home/julius/.local/state/aigorithmic-trading/reports/donchian-filters-20261006-rerun/`.
All 20 case summaries are identical to the 2026-10-04 results. This book already
anchored midnight marks, and the shared resume rule did not change any resumption
on this dataset. Full `uv run pytest -q`: **1,245 passed**.

[ADR-005](decisions/005-eth-near-sol-prospective-paper.md) freezes the ETH-NEAR-SOL basket under
Setup-2 rules (equal thirds, 60/40 Spot/Short1x, A4 filters with ADX20) at 2026-10-10 00:00 UTC
with pre-registered criteria. It supersedes [ADR-003](decisions/003-setup2-prospective-paper.md)
(Setup-2 NEAR/SOL/ZEC, never run) and [ADR-002](decisions/002-a4-donchian-prospective-paper.md)
(BTC/ETH/SOL A4, one run, 0 trades). Weekly, into new directories:

```bash
uv run python -m intraday.replay_v2.donchian_prospective collect \
  --output-root /path/to/eth-near-sol-prospective-YYYYMMDD-inputs
uv run python -m intraday.replay_v2.donchian_prospective evaluate \
  --inputs /path/to/eth-near-sol-prospective-YYYYMMDD-inputs/inputs.json \
  --report-root /path/to/eth-near-sol-prospective-YYYYMMDD
```

`collect` fetches warmup and post-freeze public data up to the latest published H4 boundary
(`--end` overrides it); a data gap fails it. `evaluate` replays from the freeze plus a full
doubled-cost replay, verifies a deterministic rerun and writes `evaluation.json` with the verdict
(`insufficient_sample`, `pass` or `fail`). A verdict never activates trading. The first run is
possible after 2026-10-10 04:05 UTC. A pre-freeze pipeline check on real data (2026-10-04 →
2026-10-07) collected all warmup without gaps in 9 seconds.

### Weekly timer

`scheduled` does both steps up to the latest published H4 end, into
`$XDG_STATE_HOME/aigorithmic-trading/reports/eth-near-sol-prospective-<YYYYMMDDTHHMMZ>{-inputs,}`.
It skips an end that is already evaluated, skips before the first post-freeze H4 bar, and stops
on a half-written directory instead of deleting it. A user systemd timer runs it every Monday at
12:00 Vietnam time; `Persistent=true` runs a missed week when the machine is next on. Every run
replays the whole window from the freeze, so a late or missed run loses no data.

The unit reads the BTC paper timer's Telegram file (`~/.config/system-trading/paper-alerts.env`).
Each fresh evaluation sends one message with verdict, days, trades, return, drawdown and profit
factor; a failed run sends the error. Skipped runs send nothing, and a failed alert never fails the run.

```bash
make install-prospective-timer      # run from the checkout the timer should use
make prospective-timer-status       # timer, next run, last 20 log lines
make uninstall-prospective-timer
```

The unit pins `WorkingDirectory` to the installing checkout, so install it from a worktree that
stays on this code (the module is not on `main` yet). Claude cloud sessions cannot run it:
Binance answers HTTP 451 (restricted location) to every endpoint from there.
