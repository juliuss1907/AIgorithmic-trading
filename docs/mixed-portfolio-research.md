# Offline mixed Spot + Perp research

Two explicit research modes share capital accounting, not signal generation:
`recorded-jev` (default, preserved 44-hour experiment below) and
`historical-quant` (24-month deterministic Perp study at the end of this guide).
Neither activates trading or changes the current Jev/confidence workflow.

Research only: no provider calls, credentials, runtime database writes, rule/gate
updates, soak changes, scheduler integration or exchange orders. This is a short
mechanics experiment, not an activation test or a 24-month profitability replay.

## Run

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --database /absolute/path/to/verified-backup.sqlite3 \
  --report-root /absolute/path/to/new-mixed-study \
  --collect-funding
```

The source needs its matching `.manifest.json`. The output must not exist.
The study freezes a private backup, loads it read-only, and verifies the source
and private evidence checksums are unchanged after replay. Funding collection
uses only the existing public Binance funding-history reader, saves raw verified
snapshots separately, and never edits the database. Without collection, funding
is unknown; it is not assumed zero. Failed collection is recorded per coin and
the study still publishes explicitly incomplete known-cost results.

## Locked experiment

- Window: 2026-09-30 04:00 to 2026-10-02 00:00 UTC (44 hours), 11 native 4h bars.
  Spot has much longer candle history, but ETH's recorded model-backed decisions
  limit this common window. It is not comparable to the earlier 24-month result.
- Shared initial capital 1,000 USDT. All positions start flat; warmup does not
  manufacture pre-existing positions. No rebalance or unused-budget transfer.
- Spot cap 60%, with BTC/ETH/SOL/NEAR/ZEC weights 40/20/20/10/10. Maximum initial
  targets are 240/120/120/60/60 USDT before ATR14 scaling.
- Spot uses Donchian 30/8, native closed 1d EMA50 trend filter and 10% coin stop.
  Signal/EMA availability and warmup validation reuse the existing Spot study.
  The Spot Jev filter is not simulated, matching the preceding Spot experiment.
- Perp cap 30% **notional**, BTC/ETH 50/50, isolated 3x. Initial target notional is
  150 USDT each, not 450 USDT. Fixed entry collateral is locked until closure;
  cap/margin/free-cash checks can reduce the next target after costs or losses.
- New entries retain at least 10% of `min(initial capital, current equity)` as
  free cash. Losses/costs may consume reserves. Notional cap 30% uses about 10%
  collateral at 3x; idle USDT can therefore be about 30%, rather than exactly 10%.
- Perp uses verified historical decisions with the frozen rules `perp-rule-v1`
  and `ethusdt-perp-baseline-v1`. Their rule hashes and parameters are reported.
  This is fixed-rule replay of recorded decisions, not reconstruction of every
  historical runtime rule change. It does not call Jev/LLM on old candles.
- Preserve confidence, quality, regime, toxic-flow, model-risk and stop filters
  through the existing scoped gate. Apply this experiment's shared allocation
  and risk limits rather than legacy single-account 1.5% daily/8% DD defaults.
- Two mixed runs: daily loss 3%/5%, both DD 10%. Two paired Spot-only controls
  use the same 60% Spot cap, shared clock, warmup and flat initial state.
- Costs per fill: Spot fee 10bps + assumed slippage 5bps; Perp fee 5bps + assumed
  slippage 5bps, plus actual recorded bid/ask spread and signed funding separately.
  These reuse the fee assumptions observed on 2026-10-01, not account fee quotes.

## Timeline, accounting and limitations

Equity is shared cash plus Spot marked value plus signed Perp unrealized PnL.
Margin locks spending capacity without deducting full Perp notional or double
counting collateral. Coin/market positions and contributions remain separate.

Funding precedes quotes at equal timestamps. Update same-timestamp quotes as a
batch; exits/risk precede entries. A decision can fill only on a subsequent fresh
quote, within 45 seconds of its creation. Latest available decision wins; no
same-quote reentry after closure. Quotes with stale books cannot fill orders.
Quote gaps are flagged, never filled with fabricated ticks.

Spot updates only at native 4h boundaries. Before a boundary, the current candle's
close/high/low cannot affect sizing, equity or Perp gates. Spot low-touch stops
are detected at candle availability, not assigned an invented intrabar time.
Reported shared DD uses known marks; Spot excursions between boundaries remain
unobserved. Prices are stored Binance prices, not verified historical Demo fills.

A shared loss trigger blocks entry immediately. Perp closes at its next fresh
quote; Spot waits for its next 4h open unless a native protective stop is detected.
Daily pause resumes only on/after the next UTC day and once all positions are
flat and DD-safe. DD has priority and remains terminal; peak equity never resets.
Exits and fees can overshoot thresholds. Perp closes at its last valid quote at
window end; Spot at the last available close. Timing differences are explicit.

Exact liquidation, historical exchange filters and partial fills are not
reconstructed. Exhausted isolated collateral invalidates full net metrics with
`unsupported_liquidation`. Missing funding or verified decisions similarly
prevents a full net-result claim; known-cost PnL remains inspectable.

## Reports and reproduction

`comparison.md` uses UTC+7; machine evidence stays UTC. `comparison.json` binds
source/evidence checksums, four run IDs and recorded Perp confidence/quote-gap
diagnostics. Private report bundles contain summary, equity curve, trades and
events with checksummed manifests. No pass/reject gate is inferred from 44 hours.

The study verifies each published summary by rebuilding `MixedConfig` from its
original serialized fields and rerunning with the same frozen inputs. To replay
offline, load `funding-inputs.json` into validated `FundingHistory` objects and
pass them as `funding_histories` to `run_study` with a new output directory and
`collect_funding=False`. Dataset/result identities do not depend on output paths
or report UUIDs. Original raw funding snapshots remain available for verification.

## Local evidence — 2026-10-03

Final study: `~/.local/state/aigorithmic-trading/reports/mixed-portfolio-20261003-final/`.
Both mixed runs and both paired controls had zero entries/trades, net PnL 0 USDT
and observed DD 0%. This is **not a pass** or evidence that Perp improves returns.
Spot had no qualifying entry during this flat-start short window. All candidate
Perp entries fell below the frozen rules' 85% confidence threshold:

| Perp coin | Recorded entry signals | Maximum entry confidence | Threshold |
|---|---:|---:|---:|
| BTC | 2,299 | 71% | 85% |
| ETH | 2,459 | 74% | 85% |

There were 5 verified historical funding settlements per coin, but no positions
to pay/receive funding. Each Perp quote history had three gaps above 45 seconds,
maximum 77.957 seconds. These limitations remain visible; thresholds were not
lowered to manufacture trades. Snapshot rules are not a live VPS-state claim.

Original and final frozen database SHA256 remained
`63a099e3fdd67d9a46578d919578bc7d9231d753babc4dcf26326a02986efbb5`.
All four published summaries and complete equity/trade/event series matched
deterministic reruns from their serialized configs. Synthetic regression tests
cover nonzero positions, costs, signed funding, locked collateral, shared halt,
deferred Spot flatten, stop/quote freshness, and cross-market event ordering.

## Historical quantitative mode — 24 months

This mode answers the longer-history portfolio question without inventing
historical Jev decisions. It uses deterministic Perp signals, **not Jev/LLM or
confidence**, and does not establish readiness of the current Perp champion.
The recorded-Jev mode, current rules, confidence proposals, gates, live database
and VPS deployment remain unchanged.

Collect public Futures inputs once:

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --mode historical-quant \
  --database /absolute/path/to/verified-backup.sqlite3 \
  --report-root /absolute/path/to/new-historical-study \
  --collect-perp
```

Replay the same inputs offline, without calling any API:

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --mode historical-quant \
  --database /absolute/path/to/the-same-verified-backup.sqlite3 \
  --report-root /absolute/path/to/new-offline-rerun \
  --perp-inputs /absolute/path/to/first-study/perp-inputs.json
```

Historical mode requires exactly one of `--collect-perp` / `--perp-inputs`.
`--collect-funding` belongs to the recorded-Jev mode; historical collection
already includes funding. Report roots must be new, never overwritten.
The default historical `--preset baseline` retains six runs. Select
`--preset stop-extension` for the eight-run wider-stop experiment:

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --mode historical-quant --preset stop-extension \
  --database /absolute/path/to/the-same-verified-backup.sqlite3 \
  --report-root /absolute/path/to/new-stop-extension-study \
  --perp-inputs /absolute/path/to/first-study/perp-inputs.json
```

This preset compares fixed 5% / full size, ATR14 × 3 / full size, and
ATR14 × 3 / two-thirds size, each with daily loss 3%/5%, plus two Spot controls.
All retain terminal DD 10%. `HistoricalConfig.perp_stop` supports
`fixed-1pct`, `atr14-2x`, `fixed-5pct`, `atr14-3x`; `perp_size` supports
`full` (default) and `two-thirds`. Neither field changes the runtime strategy.
Two-thirds scales the requested coin notional budget **before** shared
cash/exposure constraints, not the already-trimmed order. At initial equity
this targets roughly 100 instead of 150 USDT per Perp coin; fees and sequential
constraints can reduce fills further. Unused USDT stays cash, not reallocated.
It is a constant size multiplier, not automatic constant-risk sizing.

### Dataset and configurations

- Window: 2024-10-02 00:00 to 2026-10-02 00:00 UTC, 730 days. Every coin
  requires all 4,380 native 4h bars; never silently shorten the window.
- Reuse five-coin native Spot 4h/1d evidence, 31-bar / 60-day warmup. Export
  frozen study inputs rather than creating another full runtime database copy.
- BTC/ETH Futures: native contract-price 4h and 1d bars, mark-price 4h bars,
  and actual historical funding. Separate files and source-tagged checksummed
  snapshots; never insert Futures history into the Spot candle table.
- Public endpoints: `/fapi/v1/klines`, `/fapi/v1/markPriceKlines`,
  `/fapi/v1/fundingRate`. Pagination and OHLC/time coverage are validated.
  [Official API contracts](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
- Funding timestamps are preserved exactly, not snapped to an assumed 8h
  schedule. A BTC/ETH completeness guard rejects empty coverage, edge gaps and
  intervals above 8h plus 1 second of API timestamp tolerance. Missing periods
  are never interpolated or replaced with zero. This guard is not a
  reconstruction of every historical exchange funding-policy change.
- Capital/caps/weights/costs stay as specified above: 1,000 USDT, Spot 60%,
  Perp **notional** 30%, isolated 3x, reserve at least 10%. Spot uses ATR sizing;
  Perp uses fixed coin budgets on `min(initial capital, current equity)`.
- Spot remains Donchian 30/8 + native 1d EMA50/slope and 10% emergency stop.
- Perp longs: closed 4h close above highs of the preceding 30 bars, closed
  native 1d close > EMA50 > previous EMA50. Shorts mirror lows/downtrend.
  Exit on the opposite Donchian 8-bar channel, using only closed bars.
- Compare stop distance 1% with **2 × ATR14 / entry price**. ATR14 is the
  existing simple mean of true ranges, not Wilder smoothing. ATR distance is
  frozen at entry, not trailing; invalid distances outside (0, 100%) block entry.
- Six runs: both Perp stops × daily loss 3%/5%, plus two Spot-only controls
  with the same 60% cap and daily limits. All use terminal DD 10%.

### Execution conventions and evidence

Signal orders fill at the next native contract/Spot open. Perp protective stops
use **contract-price OHLC**, not mark-price: a gap fills at open, otherwise a
touch fills at the fixed stop. Touch detection is booked at Binance native
`closeTime` (end-exclusive minus 1ms); signal availability stays at the next
boundary. This keeps a 20:00 candle's closing loss in its original UTC day.
No invented intrabar detection time, trailing stop, pyramiding, or immediate
reentry at the boundary following a stop is used.

All same-time marks/funding are batched before risk checks. Actual settlements
precede same-time exits/entries. Positions touched intrabar exist in this model
until close-time detection, so funding around a stop may differ from real fills.
Mark-price values unrealized Perp PnL; contract price supplies executions.
Shared DD samples open/close, funding and costs, not coincident intrabar lows.

Daily loss flattens at the next native open and resumes only on/after the next
UTC day, flat and DD-safe. DD is terminal; equity peak never resets. Window-end
closure is explicit. Gaps and exit costs can exceed thresholds. Exhausted
isolated collateral invalidates full net-return metrics because exact exchange
liquidation is not modeled. Ideal fractional fills, fixed fee assumptions and
cash-charged slippage do not reproduce historical Binance Demo execution.

Outputs include `spot-inputs.json`, `perp-inputs.json`, initial raw collection
snapshots, six report bundles and `comparison.json` / `comparison.md`.
Failures preserve inputs and `collection-error.json`; they do not produce a
full-return study. Source/snapshot hashes are checked, configs round-trip through
JSON, and every published equity/trade/event series must match offline reruns.
Allocation order is canonical to make Decimal arithmetic reproducible.

Reports show signed funding, fees/slippage, per-market/coin and long/short
contributions, margin/exposure, halts and four continuous six-month periods.
Machine timestamps stay UTC; `comparison.md` shows the window in UTC+7.
Compare mixed returns to paired 60% Spot controls, not the preceding 65% Spot
study. Any `economic_check_only=pass` is research-only, never a gate activation.

### Accepted local research evidence — 2026-10-03

Baseline evaluator: `historical-mixed-quant-v1.1` (native closeTime / UTC-day
attribution). Authoritative study:
`~/.local/state/aigorithmic-trading/reports/historical-mixed-20261003-v1-1-final/`.
Earlier collection/debug runs are retained as preliminary evidence, not accepted
comparison results. No source data was deleted or overwritten.

| Portfolio | Perp stop | Daily loss | Net return | Observed DD | Trades | Research check |
|---|---|---:|---:|---:|---:|---|
| Spot+Perp | 1% | 3% | 17.5487% | 10.2273% | 293 | reject |
| Spot+Perp | 1% | 5% | 16.6008% | 10.0581% | 293 | reject |
| Spot+Perp | ATR14 × 2 | 3% | 0.7384% | 10.0016% | 78 | reject |
| Spot+Perp | ATR14 × 2 | 5% | 0.6340% | 10.0949% | 75 | reject |
| Spot-only control | — | 3% | 29.2802% | 7.9954% | 182 | pass |
| Spot-only control | — | 5% | 29.2802% | 7.9954% | 182 | pass |

Returns are after fees, assumed slippage and funding over the entire 24-month
account window. All four mixed accounts hit terminal DD and then remain idle:
fixed-stop runs halt in April 2026; ATR runs halt in January/February 2025.
The Spot controls trade through the full window without a daily or terminal halt.
The research result rejects these particular added-Perp configurations; it is
not evidence against the current Jev-based Perp strategy, which was not replayed.

Each Perp coin has 4,380 contract/mark bars plus warmup, 790 native daily bars
including warmup, and 2,190 actual funding settlements. The largest funding
interval is 28,800.016 seconds, within the explicit 1-second timestamp tolerance.
All six summaries and full journals match deterministic offline reruns.
Source SHA256 remains
`63a099e3fdd67d9a46578d919578bc7d9231d753babc4dcf26326a02986efbb5`.
Entry caps are not forced continuous rebalancing limits: price moves and equity
changes can push observed notional/margin percentages above entry caps.

### Wider-stop experiment — 2026-10-03

Evaluator `historical-mixed-quant-v1.2`, preset `stop-extension`, output:
`~/.local/state/aigorithmic-trading/reports/historical-mixed-20261003-stop5-atr3/`.
No API calls, Jev/LLM calls, runtime configuration changes or activation.
Spot's 10% emergency stop and all other signals/costs remain unchanged.

| Perp stop | Perp size | Daily loss | Portfolio net return | Observed DD | Perp trades | Stop exits | Research check |
|---|---|---:|---:|---:|---:|---:|---|
| Fixed 5% | Full | 3% | -0.2022% | 10.1928% | 30 | 10 | reject |
| Fixed 5% | Full | 5% | -0.0769% | 10.0800% | 29 | 9 | reject |
| ATR14 × 3 | Full | 3% | 0.0184% | 10.1252% | 29 | 9 | reject |
| ATR14 × 3 | Full | 5% | -0.2243% | 10.3432% | 28 | 9 | reject |
| ATR14 × 3 | Two-thirds | 3% | -0.7115% | 10.0247% | 38 | 12 | reject |
| ATR14 × 3 | Two-thirds | 5% | -0.7115% | 10.0247% | 38 | 12 | reject |
| Spot-only control | — | 3% | 29.2802% | 7.9954% | 0 | 0 | pass |
| Spot-only control | — | 5% | 29.2802% | 7.9954% | 0 | 0 | pass |

All six mixed accounts breach DD 10%. Full-size accounts halt in January
2025; two-thirds accounts last until 2025-04-02 02:59:59.999 UTC+7 and then
remain idle. These are whole-window returns, not 24 months of uninterrupted
trading. Size reduction postpones the halt but does not produce a passing
configuration. Daily 3% and 5% two-thirds runs have the same economic result
but different pause events, hence distinct configurations/identities.

Stop exits fall to about 31–33% of Perp trades, versus 76–77% for the old
fixed 1% benchmark. Fewer stop exits are **not** sufficient for better returns:
these Perp sleeves lose approximately 39.73–47.53 USDT net, with negative
short contributions and positive long contributions over their different
active windows. This is descriptive, not a causal long-only test. Paired
Spot controls still return 29.2802%, without a terminal halt.

Baseline fixed 1% returns were 17.5487%/16.6008%, ATR14 × 2 returns
0.7384%/0.6340% (daily 3%/5% respectively), all rejected DD. Wider stops
therefore do not improve this particular quantitative Perp setup. Changing
size changes the sequence of risk halts and trades; it cannot be interpreted
as a pure stop-distance comparison or proof about current Jev-based Perp.

All eight new summaries and complete equity/trade/event journals reproduce
offline. Separately replaying the six baseline configurations with the new
evaluator reproduces every original economic metric and journal exactly;
only version/config metadata, result identities and the additive stop-count
summary field change. Source and frozen input SHA256 match the v1.1 study:

- Source: `63a099e3fdd67d9a46578d919578bc7d9231d753babc4dcf26326a02986efbb5`.
- Spot bundle: `7102d12761db783237fb933359a4bae6fee4398c44f734ff583d7fc976acdf15`.
- Futures bundle: `5aaee92a2a5450b842e7a8a3499cfd87c485f894df7a403bffeca52cc1a61bfd`.

The private `benchmark-review.md` records the immutable baseline comparison
and receipt hashes. Neither research pass nor this comparison authorizes
soak, Demo or real-trading activation.
