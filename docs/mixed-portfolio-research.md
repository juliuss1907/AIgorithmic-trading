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

### Perp daily-policy experiment — 2026-10-03

Preset `perp-daily-policy`, evaluator `historical-perp-daily-policy-v1.0`.
This is an **offline quantitative 4h experiment**, not the current Jev/LLM
intraday worker. No new collection, model calls, runtime risk changes or
activation. Existing presets remain disabled for this additional policy.

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --mode historical-quant --preset perp-daily-policy \
  --database /path/to/verified-immutable-backup.sqlite3 \
  --perp-inputs /path/to/frozen-perp-inputs.json \
  --report-root /path/to/new-private-report-directory
```

The backup requires its matching manifest and all five coins' frozen Spot
4h/1d history. The Futures bundle requires native BTC/ETH contract/mark candles
and complete actual funding. The output directory must not already exist.
`--preset` cannot be applied to recorded-Jev mode. Defaults remain unchanged.

#### Policy and accounting contract

- Same 24-month dataset, 1,000 USDT shared portfolio, Spot 60%, Perp **notional**
  30%, reserve 10%, isolated 3x. Full Perp coin budgets; no new sizing sweep.
- The separate **virtual Perp sleeve** starts at 300 USDT and adds only Perp
  net PnL, including realized/unrealized PnL, entry/exit fees, assumed slippage
  and signed funding. It is not an exchange subaccount, margin, or allocation
  transfer. Spot PnL does not enter this policy's numerator or denominator.
- Daily return is `(Perp equity - sleeve day-start equity) / day-start equity`.
  The day baseline is the last observed equity before the UTC boundary.
  Carried positions contribute only their change in the new day, not their
  whole lifetime PnL. No external cash transfers are modeled.
- All eight mixed runs enforce Perp net daily loss **3%**. A protective stop
  closes its own position; if the resulting sleeve return breaches the daily
  limit, all remaining Perp positions are scheduled for closure. No exception
  based on the percentage of green positions or hope of recovering losses.
- For each stop (fixed 5%, ATR14 × 3), compare `none`, `target3`, `target5` and
  `trailing`. `none` means no profit cap, **not** no Perp daily-loss guard.
- `trailing` arms at +3%, then closes after a **1 percentage point** decline
  from the observed day's highest return. For example +4.5% → +3.5%. The
  peak never decreases, but the daily peak/armed state reset at the UTC day.
- Daily locks latch regardless of later PnL recovery. Resume requires a
  subsequent UTC day, no remaining Perp position and no parent halt. Never
  reenter at the same tick as a delayed flatten. Spot need not be flat.
- Parent research daily loss 3% and terminal DD 10% remain authoritative and
  may flatten **both** markets. Parent insolvency/DD/daily loss have priority.
  Runtime daily loss 1.5% / DD 8% are **not changed** by this research.
- Close/funding triggers fill at the next contract open; open triggers can
  fill at that open. Mark prices value PnL, not fills. Locked Perp opening
  requests are blocked; there are no invented live pending orders to cancel.

This samples 4h open/close plus funding/cost events, not continuous intraday
equity. The return after closure can fall below a profit target or exceed a
loss threshold because of gaps, intervening movement and closing costs.
ATR14 remains simple mean TR on closed native 4h bars, fixed at entry.

#### Results and risk interpretation

Authoritative private output:
`~/.local/state/aigorithmic-trading/reports/perp-daily-policy-20261003-24months/`.
All returns below are net of the existing fees, slippage and actual funding.
Only the ATR14 × 3 / +5% run completes the entire window without a terminal
halt among the mixed variants. Rejected runs retain idle equity after halting;
their full-window returns are not 24 months of uninterrupted trading.

| Perp stop | Profit policy | Portfolio return | Portfolio DD | Perp net USDT | Perp sleeve DD | Perp trades | Research check |
|---|---|---:|---:|---:|---:|---:|---|
| Fixed 5% | None | -0.9427% | 10.0555% | -53.9834 | 23.6190% | 31 | reject |
| Fixed 5% | +3% | 22.7959% | 10.0438% | 66.6706 | 14.2127% | 126 | reject |
| Fixed 5% | +5% | -1.3365% | 10.2108% | -58.8404 | 24.0700% | 33 | reject |
| Fixed 5% | Trailing | 19.6936% | 10.0848% | 38.2317 | 17.4105% | 121 | reject |
| ATR14 × 3 | None | -0.8208% | 10.0808% | -53.4050 | 23.7982% | 30 | reject |
| ATR14 × 3 | +3% | 22.2744% | 10.1980% | 70.2164 | 14.4676% | 126 | reject |
| ATR14 × 3 | +5% | **40.0815%** | **9.5172%** | **96.6608** | **21.6093%** | **144** | pass |
| ATR14 × 3 | Trailing | 18.5600% | 10.1061% | 20.3449 | 17.6582% | 119 | reject |
| Spot-only control | — | 29.2802% | 7.9954% | — | — | 0 | pass |

The ATR14 × 3 / +5% variant ends at 1,400.8145 USDT. Perp itself contributes
96.6608 USDT (BTC 44.1738, ETH 52.4870; long 61.4261, short 35.2347) after
21.4271 fee + 21.4271 slippage + 4.7511 signed funding costs. Spot contributes
304.1537 USDT. Compared with the Spot-only control, the total improvement is
10.8012 percentage points, **not all direct Perp PnL**: shared cash/risk also
changes Spot allocations and its economic path.

It hits the +5% profit guard on 12 days and the Perp loss guard on 17 days;
24 of 144 Perp trades exit at protective stops. Its Perp sleeve return is
32.2203%, but sleeve DD is **21.6093%** and its worst observed day is
**-6.4805%**, despite a 3% trigger. The 9.5172% figure is the **combined
portfolio** DD. This experiment does not establish a guaranteed daily loss
cap, a guaranteed daily income, or a low-risk standalone Perp strategy.

Perp six-month continuous returns are -8.5925%, +24.1728%, +7.1172% and
+8.7503%; they are not four independently reset or held-out tests. The
combined portfolio also has a negative third six-month period (-1.3450%).
This is an ex-post comparison on previously inspected history, not an
independent holdout and not automatic selection/promotion of the best run.

Outputs include nine immutable report bundles, `comparison.json/md` and the
frozen inputs. Each full equity curve records Perp baselines, return, peak and
lock state. Events record arming, triggers, after-flatten return, resume and
blocked requests. Summaries include sleeve costs/DD, profit/loss/trailing
counts, worst day and both portfolio/sleeve six-month periods. Fewer than six
Perp trades or a never-triggered profit policy is marked inconclusive evidence.
Incomplete funding/unsupported liquidation never produces a full net return.

All nine summaries and complete journals reproduce from serialized configs.
A separate process also verifies these nine runs, the eight wider-stop runs,
and six original baseline runs: **23 complete journals**, unchanged economic
results for the old configurations, and unchanged source/input hashes.
Focused research regression: 112 passed. Full project regression:
`uv run pytest -q` — **1,080 passed**, 29 existing dependency deprecation
warnings. No tests disabled and no new dependencies added.

- Source SHA256: `63a099e3fdd67d9a46578d919578bc7d9231d753babc4dcf26326a02986efbb5`.
- Spot bundle SHA256: `7102d12761db783237fb933359a4bae6fee4398c44f734ff583d7fc976acdf15`.
- Futures bundle SHA256: `5aaee92a2a5450b842e7a8a3499cfd87c485f894df7a403bffeca52cc1a61bfd`.
- Dataset checksum: `3fe0072da5a6f5446e8aadf1676c9df3b65610301855389ac7bac6c9e767a645`.
- Comparison SHA256: `e0dde4adb96e37a4696f2fbd4bea4ca2cb2cfeab7b0a3585a0cad089ea1bafbf`.
- ATR14 × 3 / +5% result: `f6983c002074499541ba6b88a4c7e6fccdc03e2a6acfe158e886298611e6fc9e`.

Default-off legacy runs retain their existing config hashes, result identities
and complete journals under the v1.2 evaluator. Research `pass` remains
descriptive only, not an official gate or permission for soak/Demo/real trades.

### Reinvest accumulated equity — paired research (2026-10-03)

The earlier +40.0815% mixed return accumulates PnL but does **not** grow entry
budgets above initial capital: its sizing base is `min(initial capital, equity)`.
The opt-in `HistoricalConfig.capital_growth='equity'` removes that ceiling for
both markets. Spot cap 60%, Perp **notional** cap 30%, reserve 10% and coin
weights now use current mark-to-market **portfolio** equity at each new entry.
Realized and unrealized PnL, including known costs/funding, affect that base.
Losses shrink budgets too. Cash/margin/exposure constraints still apply.

This is entry-time equity sizing, **not** forced daily rebalancing, resizing
existing positions, or a guaranteed daily compounded cash yield. The separate
virtual Perp sleeve remains initially 300 USDT plus only its own net PnL;
its day-start equity still determines +5%/-3% triggers. Sizing does not transfer
Spot profits into that accounting sleeve or redefine the daily denominator.

The four-run preset fixes the previously selected mixed setup: ATR14 × 3,
full Perp size, profit target +5% and Perp daily loss 3%, parent daily loss 3%
and terminal DD 10%. It compares capped versus equity sizing for that mixed
book **and** its paired Spot-only control. Signals, allocations, leverage,
fees/slippage, actual funding, native fills and frozen 24-month data are unchanged.

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --mode historical-quant --preset perp-daily-compounding \
  --database /path/to/verified-backup.sqlite3 \
  --perp-inputs /path/to/frozen/perp-inputs.json \
  --report-root /path/to/new-private-directory
```

The preset is historical-only. No model calls, source collection or runtime
activation are needed with frozen inputs. Default capped configs omit the new
field in published metadata, keeping old evaluator versions, result IDs and
complete journals identical. Equity runs use `historical-equity-growth-v1.0`.

Authoritative private output:
`~/.local/state/aigorithmic-trading/reports/perp-daily-compounding-20261003-24months/`.

| Portfolio | Entry sizing | Net return | Final equity USDT | Portfolio DD | Perp net USDT | Perp sleeve DD | Research check |
|---|---|---:|---:|---:|---:|---:|---|
| Spot+Perp | Capped (old) | +40.0815% | 1,400.8145 | 9.5172% | +96.6608 | 21.6093% | pass |
| Spot+Perp | Current equity | -1.6271% | 983.7290 | 10.0863% | -60.7963 | 23.9790% | reject |
| Spot-only | Capped (old) | +29.2802% | 1,292.8024 | 7.9954% | — | — | pass |
| Spot-only | Current equity | +32.1198% | 1,321.1976 | 9.4107% | — | — | pass |

The compounded mixed run breaches the terminal DD guard at
`2025-01-28T03:59:59.999Z` (10:59:59.999 UTC+7), about 118.17 days after
starting. It closes remaining positions at the next native open and remains
idle for the rest of the window: **this is not 24 months of continuous trading**.
It has 78 closed trades, including 33 Perp trades. Spot earns +44.5254 USDT
but Perp loses -60.7963 (BTC +4.9333, ETH -65.7296). Entry bases reach
1,094.0811 USDT before halting. Larger growth-based budgets and altered daily
locks/exposure paths can cross a DD boundary that the capped path avoids;
reinvestment is not intrinsically a profitability improvement.

The equity Spot-only control does trade the full window and improves return
by 2.8395 percentage points, with higher observed DD (9.4107% versus 7.9954%).
The mixed equity result must not be credited with later market gains that its
terminal halt prevented it from trading. Its worst observed Perp day is
-6.8126%, despite the unchanged 3% trigger; 4h/funding sampling, gaps and exit
costs still do not establish a hard intraday loss cap.

All four complete summaries and journals reproduce offline. An independent
process also verifies that both capped runs match the earlier daily-policy
reports byte-for-byte across all three journal series. Source backup, Spot
bundle, Futures bundle and previous comparison hashes remain unchanged.
Focused research regression: 124 passed. Full project regression:
`uv run pytest -q` — **1,092 passed**, 29 existing dependency deprecation
warnings. No new dependencies or disabled tests.

- Comparison SHA256: `a8f374b0fa089aea5e9ce5ccc5f75fe86aeb6ba170f084e9ebfb37446fb05a96`.
- Equity mixed result: `b8062f1715548a7939793778740b1efaa6ba8127fa233efd37de6a86f6943df0`.
- Equity Spot-only result: `9d83e4e14b7c1cc55f8cf7a16757caaff24b033d9b99927b4bb76d65a496bc8a`.

These are ex-post research comparisons, not independent holdouts, official
gates or automatic selection. No runtime rule, risk limit, wallet, worker,
database, soak, Demo/real trading state, push or deployment was changed.

### No DD lock versus initial-capital floor (2026-10-03)

Historical-only `drawdown_policy` has three modes; the runtime and recorded-Jev
paths do not accept this setting:

- `terminal` (default): existing 10% drawdown from the observed equity peak.
- `observe-only`: measure peak DD but do not close, lock or restrict daily
  resumption because of DD. Stop-losses and daily pauses remain active.
- `initial-capital`: terminal stop when shared marked portfolio equity is
  at or below `initial capital × (1 - max_drawdown)`. At 1,000 USDT / 10%,
  the floor is **900 USDT**, never profit-trailed or daily-reset. A prior
  1,200 USDT peak followed by 950 USDT does not activate this floor.

The same capital policy is consulted during risk checks and daily resumption.
The new floor has its own `initial_capital_loss` event/exit reason and latches
for the rest of the replay even if prices subsequently recover. It closes
both Spot and Perp. Equity includes unrealized PnL and recorded costs/funding;
no realized sale is required to trigger it. Delayed native-open fills, gaps
and closing costs may make the actual loss exceed 100 USDT. Unsupported
isolated collateral and exhausted Perp capital still prevent a fabricated
profitable continuation or full-return claim.

New nondefault runs use `historical-capital-guard-v1.0` and include explicit
policy, equity minimum, maximum loss relative to initial capital, and first
peak-DD/floor crossing timestamps. The default field is omitted from published
configs, preserving all older hashes, versions and journals. Peak equity is
never reset. The research check remains net-positive, peak DD below 10% and
at least six closed trades: respecting the 900 USDT floor is a **different
criterion**, not an override of that check or official gate acceptance.

Run each group using the same frozen inputs and a separate new directory:

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --mode historical-quant --preset perp-daily-compounding \
  --drawdown-policy observe-only \
  --database /path/to/verified-backup.sqlite3 \
  --perp-inputs /path/to/frozen/perp-inputs.json \
  --report-root /path/to/new-observe-only-directory
```

Use `--drawdown-policy initial-capital` and a different report directory for
the initial-capital group. Both groups were run as **two parallel processes**,
with four matched configurations each (mixed/Spot-only × capped/equity sizing).
ATR14 × 3 full Perp size, target +5% / loss -3%, parent daily loss 3%, allocation,
fees, funding, signals, native execution and the 24-month window are unchanged.

Authoritative private root:
`~/.local/state/aigorithmic-trading/reports/perp-capital-guard-comparison-20261003-24months/`.
It contains eight new immutable reports in `observe-only/` and
`initial-capital/`, plus combined `comparison.json/md` referencing the four
unchanged peak-DD reports from the prior experiment.

| Portfolio | Sizing | Peak-DD terminal reference | No DD lock | Initial-capital floor | Peak DD without peak lock |
|---|---|---:|---:|---:|---:|
| Spot+Perp | Capped | +40.0815% | +40.0815% | +40.0815% | 9.5172% |
| Spot+Perp | Equity growth | -1.6271% | +29.6198% | +29.6198% | 15.5217% |
| Spot-only | Capped | +29.2802% | +29.2802% | +29.2802% | 7.9954% |
| Spot-only | Equity growth | +32.1198% | +32.1198% | +32.1198% | 9.4107% |

All eight new runs finish the window without a capital terminal halt. The
900 USDT floor **never triggers**: the compounded mixed minimum is 947.5382
USDT (5.2462% below initial capital), despite peak DD of 15.5217%. Its first
peak-DD breach is still 28 January 2025, but this no longer prevents subsequent
trading. Each matched no-lock/floor pair has identical complete economic
journals; the different policy is still recorded with a distinct result ID.
This dataset therefore does not establish the economic effect of actually
triggering the initial-capital floor; its activation is covered by tests.

The equity mixed book finishes at 1,296.1976 USDT after 344 trades: Spot
contributes +327.4754, Perp **-31.2778** (BTC -4.0413, ETH -27.2365). Perp
has 161 trades, 27 daily loss pauses and 18 daily profit pauses, with **32.5596%
sleeve DD** and a worst observed day of -6.8126%. Portfolio DD and Perp sleeve
DD must not be conflated. Spot-only equity growth finishes at 1,321.1976;
adding Perp reduces total return by 2.5000 percentage points versus that
paired control, including indirect shared-cash/risk effects.

The eight mixed/control runs comprise six research passes and two rejections:
both compounded mixed modes reject the unchanged **peak-DD** check. They are
profitable and respect the initial-capital floor, but are not evidence of a
profitable standalone Perp strategy or automatic permission to trade.
The earlier -1.6271% peak-halt reference traded only about 118 days, not the
full 730; the eight new runs do reach the full window.

An independent process reproduces all **12 full summaries and journal sets**,
including the four previous references, verifies that the two new groups'
matched economic journals are identical, and confirms original source/input
and prior comparison checksums unchanged. Tests cover exact floor boundaries,
unrealized PnL, native contract rather than mark fills, both-market flattening,
fixed versus peak basis, next-day resumption, terminal latching, Perp daily
guards, collateral protection, config validation and historical-only CLI use.

- Focused research regression: **146 passed**.
- Full project regression: `uv run pytest -q` — **1,114 passed**, 29 existing
  dependency deprecation warnings. No disabled tests or new dependencies.
- Combined comparison SHA256: `b36071536c2c3e0abe8225780d262f6a4f57b8b98aa2d512abe0d76ca16b86ea`.
- Equity no-DD-lock result: `35a90d9b7cbb4162ee5d02e8a130dfde3ae11bf564b7b6dbdb9a9bdd3d070d46`.
- Equity initial-capital result: `7f6b4d4415c924f431ff2a59227fdb80a4d620bfa9e58ec764c1a636b7a64632`.

Only research code, tests and documentation changed locally. Runtime rules,
risk settings, database, workers, soak and Demo/real trading remain untouched;
no push, merge, deployment or activation occurred.
