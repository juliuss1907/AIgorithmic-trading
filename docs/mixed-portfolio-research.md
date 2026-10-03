# Offline mixed Spot + recorded-Jev Perp research

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
