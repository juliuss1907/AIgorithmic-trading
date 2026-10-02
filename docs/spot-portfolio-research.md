# Spot portfolio research: BTC / ETH / SOL

Offline research only. This runner does not register candidates, update gates,
start soak, call Jev/LLM, or access execution credentials. It does not run from
the scheduler or dashboard. Single-asset replay remains unchanged.

## Run

```bash
uv run python -m intraday.replay_v2.portfolio_research \
  --database /absolute/path/to/verified-backup.sqlite3 \
  --report-root /absolute/path/to/new-portfolio-study \
  --collect-missing-1d
```

The database must have its matching `.manifest.json` backup manifest. The output
directory must not exist. The window is fixed to 2024-10-02 00:00 UTC through
2026-10-02 00:00 UTC. All three registered Spot assets need contiguous native
4h history covering that window plus 31 warmup bars. A missing 4h bar fails the
study; it is not filled forward.

The optional collection flag fetches **only missing native Binance 1d bars**
into a private SQLite copy, including 60 warmup days for EMA50. Existing bars are
not refetched or overwritten. Without the flag, daily history must already be
complete. The final verified backup is frozen before any simulation starts.
Collection errors fail the study; partial artifacts remain for inspection and a
retry must use a new output directory.

## Locked experiment

- One 1,000 USDT cash account; BTC/ETH/SOL weights 50/25/25.
- Total entry cap 60% of `min(initial capital, current equity)`, divided 30/15/15%.
  ATR14 scales each target once: `min(1, 0.02 / ATR_fraction)`.
- Signals do not transfer unused coin budgets. Exits precede entries. If existing
  positions consume the total cap, simultaneous new orders scale proportionally.
  Holdings are not rebalanced, and price appreciation can exceed the entry cap.
- Four variants: 30/8, 30/6, 30/10, 30/8 plus native 1d trend filter. The last
  requires close > EMA50 and EMA50 > previous day's EMA50. EMA is seeded with the
  first 50 available warmup closes. Entry uses only candles available at that open.
- Each variant uses daily loss 3%/5% crossed with DD 8%/10%, giving 16 runs.
- 10bps fee plus 5bps assumed cash-charged slippage **per fill**, with ideal
  fractional quantities. Historical exchange filters/quotes are not reconstructed.
- Emergency stop per coin is 10%; gaps fill at open, low-touch fills at stop.
  Intrabar stops are recorded at bar-close detection time, not a claimed tick time.
- Shared equity is cash plus all marked holdings. Shared daily/DD guards are
  evaluated at native 4h opens/closes and after boundary fills. Intrabar stop
  realizations are evaluated together at close; lows are never synchronized to
  invent a portfolio mark. Between-sample DD is unknown, and gaps/fees can overshoot.
- A shared loss guard flattens all coins. Daily loss resumes at next UTC day only
  when flat and below the DD limit. DD has priority, remains terminal, and the
  high-water mark is never reset. Remaining bars are processed as flat cash.

## Reports and interpretation

`comparison.json` binds source/evidence checksums and all 16 report IDs.
`comparison.md` shows the matrix and UTC+7 halt times. Each `reports/<run_id>`
bundle has a verified manifest, summary, equity curve, fills, and events. The
summary includes each coin's PnL contribution, sampled DD, fees, exposure, pause
counts, active time until terminal halt, and four consecutive six-month periods
(not four fresh accounts). Timestamps in machine-readable evidence remain UTC.

The research check requires positive net return, observed DD **strictly below**
the configured limit, at least six closed trades across the portfolio, and no
allocation violations. This is not an official per-asset gate or activation
approval. All 24 months have already been inspected: no independent holdout is
claimed. These outputs are not directly comparable to old single-asset studies
with different caps and intrabar loss checks. Forward validation is still needed.

To reproduce a report, build `PortfolioConfig` from the original fields in its
`config` object. Do not reconstruct policy inputs from rounded comparison-table
percentages: Decimal strings `0.10` and `0.1` have equal numeric values but different
serialized identities, consistent with the existing replay fingerprint format.

## Local verification — 2026-10-02

- Full regression: **940 passed**, 18 existing dependency deprecation warnings.
- Focused portfolio suite: **22 passed**, including serialized-config roundtrip,
  source preservation, native daily gap collection, and 16 unique report identities.
- Completed all 16 real-data runs, then independently re-executed all 16 from
  each published config. Result IDs and every summary matched exactly. This is
  a deterministic rerun, not an independent statistical validation.
- All three assets have 4,380 replay bars, full coverage, and native daily warmup.
  Exactly 423 missing native daily bars per coin were appended to the private copy.
- Original immutable source SHA256 before/after:
  `8827a7c5f96909214226307d85fa2df095d6f28c1d3dd18ede349760cc29ec3f`.
- Final frozen research evidence SHA256:
  `704358d0f48f97e6fa28bd2d2129b567e8d5caac318f04aff8bea6493874385b`.
- Artifacts: `~/.local/state/aigorithmic-trading/reports/spot-portfolio-20261002-60pct/`.

Of 16 runs, two pass the research check: 30/8 + EMA50 1d with DD 10%, at either
daily loss 3% or 5%. Both run the complete 730-day window with no daily pause or
terminal halt: net return 23.895470%, observed DD 8.615202%, 99 closed trades.
The other 14 runs terminal-halt and fail the configured DD check. Their positive
ending returns do not override the risk failure.

The passing portfolio's net contributions are BTC 113.608056 USDT, ETH
39.702517 USDT, SOL 85.644129 USDT, reconciling to 238.954702 USDT net profit.
Fees are 38.396309 USDT and assumed slippage 19.198154 USDT. Its six-month returns
are +8.029699%, +13.409809%, -3.082605%, +4.342181%, so gains are not uniform.

Self-review covered accounting/risk correctness, input/provenance validation,
runtime isolation, deterministic ordering, and bounded data collection. No
existing runtime module, schema, provider, gate, registered rule, or campaign
was changed; no VPS deployment, push, merge, activation, or orders were performed.
