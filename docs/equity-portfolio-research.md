# Daily technology-stock portfolio research

Research-only USD cash-account experiment for NVDA, META, MSFT and GOOGL.
It does not add stocks to AIGT runtime, broker adapters, lifecycle gates,
confidence review, Demo trading or the VPS deployment. Existing stock and
crypto engines/configuration are unchanged.

## Reproduce

Collect the four public Yahoo daily histories once into a **new** private root:

```bash
uv run python -m lab.equity_study \
  --report-root /absolute/path/to/new-equity-study --collect
```

Replay without any network call:

```bash
uv run python -m lab.equity_study \
  --report-root /absolute/path/to/new-offline-study \
  --inputs /absolute/path/to/first-study/inputs.json
```

Exactly one of `--collect` / `--inputs` is required. Existing report roots
are never overwritten. The downloader is the existing `lab.data` Yahoo
collector; this research has its own bounded four-ticker configuration and
does not expand the application dataset allowlist or mutate its catalog.

`inputs.json` contains source/retrieval metadata, all raw columns/rows and a
checksum. Per-symbol `*-raw.json` files preserve initial downloads. Validation
requires every expected cash-market session, complete warmup, finite OHLC,
positive prices, valid OHLC bounds and nonnegative volume. Missing sessions
are errors, never filled. The `XNAS` schedule from `exchange_calendars`
supplies real regular-session opens/closes, DST, holidays and early closes.

The four rule configurations and one ungated buy-and-hold reference publish
separate result-ID JSON files, including full equity/trade/event journals,
and `comparison.json` / `comparison.md`. JSON configs round-trip before every
full-report reproduction. Frozen source/output input hashes must not change.

## Assumptions

- Evaluation: 2024-10-02 through 2026-10-01 inclusive; end-exclusive
  2026-10-02. Warmup starts 2024-07-01. Native **1d**, not crypto 4h bars.
- Initial capital **1,000 USD**, long-only, no leverage, shorting or funding.
  Equal 25% maximum entry budgets, gross entry cap 100% of
  `min(initial capital, current equity)`. Entry costs and ATR sizing can leave
  substantial idle cash. Profits do not increase those entry budgets.
- Donchian 30-bar breakout / 8-bar exit. The signal candle is excluded from
  its own channels. EMA50 is seeded with 50 closes; require close > EMA50 >
  previous EMA50. Signals from a closed session execute at the next session
  open, never on their own closing price.
- ATR14 is the crypto Spot helper's **simple mean true range**, not the
  existing stock lab strategy's Wilder-style smoothing. Entry size multiplier
  is `min(1, 2% / ATR%)`; holdings are not periodically rebalanced.
- Protective stop: fixed 5%, or 3 × ATR14 from the preceding closed session.
  The distance is frozen at entry, not trailing. A gap fills at the current
  open; otherwise a daily low touching the stop fills at that stop price,
  detected/booked at session close. Closed positions cannot immediately
  reenter at the same open.
- **Assumed**, not broker-verified: 5 bps commission plus 5 bps cash-charged
  slippage per fill. No second implicit slippage adjustment to fill prices.
  No tax, FX, specific regulatory charges or actual broker execution model.
- Prices are `OHLC × Adj Close / Close`: synthetic total-return price units,
  not executable historical dollar quotes or actual shares. Dividend effects
  are implicit; no separate dividend credit. Fractional synthetic units are
  assumed, with no partial-fill/minimum-order constraints.
- Daily loss 3%/5%, terminal DD 10%. Daily loss is measured from preceding
  session close to current open/close/cost observations, including overnight
  gaps. A close-triggered halt flattens at the next session open; an opening
  gap can trigger flattening at that opening price. Daily pause resumes only
  next session and flat; equity peak never resets. DD terminal halt leaves
  the remaining window idle. Gaps/costs can overshoot thresholds.
- Risk uses observed open/close/fill-cost values, not coincident intraday
  lows across stocks. Exact intraday stop timing/DD remain unknown.
- Buy-and-hold buys equal budgets at the first evaluation open and sells
  at the last session close: same cash/cap/costs, but no ATR sizing, stops,
  periodic rebalancing or risk halts. It is a reference, not a gated candidate.

This selected-stock, ex-post research is not an independent holdout. A
research pass means positive net return, observed DD strictly below 10%,
and at least six closed trades; it does **not** authorize trading.
Do not directly rank against the crypto 4h / 60%-Spot-cap experiments:
bar frequency, capital deployment, trading calendar and costs differ.

## Verified local results — 2026-10-03

Evaluator: `equity-daily-cash-research-v1`.
Collected study: `~/.local/state/aigorithmic-trading/reports/equity-tech-20261003-24months/`.
Independent offline process: `~/.local/state/aigorithmic-trading/reports/equity-tech-20261003-offline/`.
Use the offline root's `comparison.md` as the verified human-readable report.

Each ticker has **566** validated daily rows, including **65** warmup
sessions and **501** evaluation sessions. All five complete reports,
including journals, reproduce in memory and in the separate offline run.
The five report files match byte-for-byte across processes. Frozen input
SHA256 is `c967a1ab87cc5eaf72dde0ad8a35125938c6ae55654c0cf1201b7f84ef484ed4`;
dataset checksum is `c785cd2953124e71fbd1228eca229904b5c501f0b7c7a5576109bf4ab9e8dc1c`.
Verification: `uv run pytest -q` — **1,049 passed**, with 29 dependency
deprecation warnings. No push, merge, deployment or activation was performed.

| Strategy | Stop | Daily loss | Net return | Observed DD | Trades | Stop exits | Research check |
|---|---|---:|---:|---:|---:|---:|---|
| Donchian30/8 + EMA50 | Fixed 5% | 3% | 13.1456% | 6.4300% | 33 | 13 | pass |
| Donchian30/8 + EMA50 | Fixed 5% | 5% | 13.1456% | 6.4300% | 33 | 13 | pass |
| Donchian30/8 + EMA50 | ATR14 × 3 | 3% | 8.9220% | 8.3907% | 33 | 9 | pass |
| Donchian30/8 + EMA50 | ATR14 × 3 | 5% | 8.9220% | 8.3907% | 33 | 9 | pass |
| Buy-and-hold reference | — | — | 63.1339% | 29.0920% | 4 | 0 | not gated |

All four rule runs finish the entire window without a daily or terminal
halt. Daily limits 3%/5% therefore produce identical economics, while their
different policies retain separate config/result identities. The fixed 5%
setup performs better than ATR14 × 3 in this particular sample; fewer stop
exits do not imply greater profitability.

With fixed 5%, final equity is **1,131.46 USD**: net contributions GOOGL
77.36, NVDA 29.43, MSFT 18.39 and META 6.28 USD. Total commission and
slippage charges are 12.28 USD. ATR14 × 3 ends at **1,089.22 USD**, with
12.23 USD total costs. These contributions are USD PnL, not standalone
per-stock return percentages.

Buy-and-hold earns more in this selected window, but incurs much higher
observed DD and deploys more capital than ATR-sized active entries. The
comparison includes both signal and sizing effects; it does not establish
future returns or directly validate any production strategy.
