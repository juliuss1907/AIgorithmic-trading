# Perp intraday timeframe research

Local, deterministic research only. This does not modify runtime rules,
Jev/LLM decisions, confidence thresholds, official gates, workers, soak,
Binance Demo/real accounts, or authorize activation. No automatic winner.

## Paired experiment

Default window: **2024-10-29 00:00 UTC → 2026-10-02 00:00 UTC**, 703 days;
both boundaries are 07:00 UTC+7. UTC is used for bars and trading days.
The known native opening discrepancy on 2024-10-28 remains outside this
active window; no old evidence is normalized or overwritten.

| Case | Perp signal / Donchian exit | Trend | Initial stop | Trailing | Periodic Perp risk | Parent risk |
|---|---|---|---|---|---|---|
| A | H4, 30/8 | D1 EMA50 direction/slope | ATR14 H4 ×3 | H1 | H4 | H4 |
| B | H1, 30/8 | H4 AND H8 EMA50 direction/slope | ATR14 H1 ×3 | H1 | M15 | H4 |
| C | H1, 30/8 | H4 AND H8 EMA50 direction/slope | ATR14 H1 ×3 | M15 | M15 | H4 |

Funding and actual fill-cost events additionally check both risk books.
The B/C periodic M15 observation **must not call the parent portfolio guard**:
it cannot update the parent's peak, DD, last equity or daily baseline.
Parent risk therefore remains H4 plus funding/cost events, even though
trade costs can produce additional checkpoints between H4 boundaries.

All cases start with 1,000 USDT: Spot600, Perp **notional**300, reserve100;
Perp isolated3x, BTC/ETH50/50. Spot BTC/ETH/SOL/NEAR/ZEC40/20/20/10/10.
Sizing uses each sleeve's own realized PnL after costs and settled funding,
not unrealized PnL; no profit transfers or rebalance of existing positions.
Spot H4 Donchian30/8, D1 trend, ATR sizing and 10% emergency stop are unchanged.
Shared cash/reserve and parent halts can nevertheless change Spot fills.

Perp daily loss is −3% of its own marked day-start sleeve equity; parent
daily loss is −3% of portfolio day-start equity. No fixed daily profit cap.
DD is observe-only; the research economic check still requires positive
return, DD<10%, at least six closed trades, complete funding and supported
collateral. This check is not official gate approval.

## Native observations and execution

- Entry uses the previous closed H1 bar and the preceding30-bar channel;
  entry fills at the next native H1 open. Donchian exit8 also uses H1.
- H4/H8 native trends seed EMA50 with the first50 closes. Long requires
  close>EMA50 and rising EMA on both; short requires close<EMA50 and falling
  EMA on both. Neutral/disagreement blocks entry. Future/unclosed bars
  cannot contribute. H8 is downloaded, not synthesized from H4.
- ATR14 is simple mean true range, not Wilder/RMA; ×3 distance is frozen
  at entry. Contract gap detection is M15 open; OHLC touch execution is at
  the stop price detected at M15 close. Exact intrabar order is unknown.
- Net trade trailing includes entry fee/slippage, estimated exit costs and
  already settled funding, divided by **frozen entry notional**, not margin.
  Arm at +3%; floor is observed peak−3 percentage points and never lowers.
  Update only native closes, H1 for B and M15 for C; highs/lows never arm it.
  Close breach latches to the next open of that trailing interval, including
  rebound. An open gap through the old floor exits at that open.
- Perp daily/collateral observations use native M15 mark-price open/close.
  Close/funding triggers flatten Perps at next M15 contract open; triggers
  at open, including new fill-cost triggers, flatten at that same open.
  Parent-triggered Spot flatten waits for native H4 open; Perp waits M15.
- At coincident opens: parent → Perp daily → ATR gap → trailing → H1
  Donchian → new entries. A position cannot reopen on its exit tick.
  Daily Perp resume requires nextUTCday AND Perpflat AND parentsafe,
  never the same tick as flatten. Trailing state does not reset overnight;
  positions may hold across days. Window end explicitly closes all positions.

Spot fee/slippage10/5bps and Perp5/5bps per fill remain unchanged; actual
historical funding settles at returned timestamps. Fee source assumptions
are inherited from existing research, not verified historical account fees.

## Immutable inputs and command

Five supplemental series per Perp coin:
native tradeH1 (31-bar warmup), tradeH4/tradeH8 (60-bar warmup each),
tradeM15 and markM15 (active window). All snapshots validate source, symbol,
interval, coverage, OHLC, finite values, gaps, duplicates and checksums.
Active common native contract boundaries and frozen H4 mark boundaries
must match **exactly**. A discrepancy stops publication, not silently
shortens/normalizes the experiment.

Fully offline replay with a **new** output directory:

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --mode historical-quant --preset perp-intraday-timeframes \
  --database /path/to/original-verified-backup.sqlite3 \
  --perp-inputs /path/to/703days-inputs/perp-inputs.json \
  --intraday-inputs /path/to/intraday-inputs.json \
  --baseline-reference /path/to/original-A-reports/RUN_ID \
  --report-root /path/to/new-private-directory
```

To explicitly collect, replace `--intraday-inputs` with `--collect-intraday`.
Optional `--reuse-perp-inputs` and `--reuse-trailing-inputs` reuse frozen
H4/H1 history; only a missing native prefix is collected. Fully covered
parent snapshots become exact row subsets. Lineage records parent IDs,
file hashes and any warmup snapshot; composite acquisition metadata is
distinguished from original parent metadata. Reused active rows are not
refetched or rewritten. The 703-day run reused the earlier730-day parents
for H1/H4 warmup and collected H8/M15 separately.

Exactly one `--collect-intraday` / `--intraday-inputs` is required. The
base `--perp-inputs` must already be frozen, not `--collect-perp`.
Reuse flags require collection. `--baseline-reference` additionally
requires exact original A result ID, summary and all three journal hashes.
`--start`/`--end` may set an explicit consistent window; other presets keep
their defaults. Only observe-only DD is allowed for this experiment.

## Publication and passive common-grid audit

Each case publishes `summary.json`, `equity_curve.jsonl`, `trades.jsonl`
and `events.jsonl` in a private immutable report bundle. B/C also publish
separate fine Perp equity sidecars. Summary and complete journals must
reproduce offline before `comparison.json`/`comparison.md` publication;
source SQLite and exported evidence hashes must remain unchanged.

Engine DD reflects each engine's own risk checkpoints. A separate passive
audit revalues **all three** frozen fill/cash journals on identical native
M15 mark open/close timestamps, with latest already-known H4 Spot prices.
It does not introduce new Spot prices, feed back into execution, or change
A's original identity/summary. Immediate entry-and-flatten trades are
cash-only at post-event samples, not surviving positions. Audit curves and
fine Perp sidecars have independent checksums in `comparison.json`.

Common-grid DD still is not exact intrabar DD: Spot remains H4 as-of,
unknown ordering inside M15 bars remains, and gaps/exit costs can overshoot
loss/trailing floors. Fractional ideal fills lack partial fills, instrument
filters, verified Demo parity and exact liquidation. This is an ex-post
comparison, not an independent holdout or current Jev/confidence replay.

## Completed 703-day result — 2026-10-03

All three use the same frozen base dataset and complete actual funding.
Capital is restarted from 1,000 USDT, not carried from earlier experiments.

| Case | Final USDT | Portfolio return | Spot net USDT | Perp net USDT | Engine portfolio DD | Common M15 portfolio DD | Perp trades | Trailing exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A H4/D1, trailingH1 | 1,419.6712 | +41.9671% | +381.7541 | +37.9171 | 13.0254% | 13.0313% | 148 | 65 |
| B H1/H4/H8, trailingH1 | 1,281.5854 | +28.1585% | +353.0909 | −71.5055 | 14.4062% | 14.4062% | 507 | 48 |
| C H1/H4/H8, trailingM15 | 1,276.7334 | +27.6733% | +353.0764 | −76.3430 | 14.2573% | 14.2573% | 512 | 70 |

The faster entry setups do **not** improve this dataset: B/C finish
138.0858/142.9378 USDT below A. All reject the unchanged DD<10% research
check; none is an official acceptance or deployment recommendation.
Portfolio profitability in B/C comes from Spot, not profitable Perp.

Perp gross price PnL is +91.2691/+67.1168/+64.6914 USDT for A/B/C, while
Perp fee+slippage rises from **48.2103 to 133.8432/136.4191 USDT**; actual
funding costs are 5.1417/4.7791/4.6153 USDT. Thus B/C's positive gross
price PnL is consumed by more frequent trading costs. Both long and short
Perp net PnL are negative for B/C. This describes these assumed costs and
observed trades, not a claim that all H1 strategies are unprofitable.

Mean Perp holding time falls from61.4054h to20.1923h/19.2954h. Donchian
exits rise48→341/326, ATR touch stops21→107/107. Perp daily loss pauses
are12/9/8; finer daily risk does not eliminate cumulative drawdown or
gap/exit-cost overshoot. Worst observed Perp day is −4.7169% for A versus
−3.5442% for B/C, not a guaranteed −3% cap.

Perp sleeve DD (engine / passive common-grid) is **22.3115% / 24.7835%**
for A, **37.6345% / 37.6345%** for B and **37.4809% / 37.4809%** for C.
Sampling A's unchanged book more frequently reveals deeper Perp drawdown
without changing any A fills or original summary. Spot rules stay unchanged;
shared constraints reduce B/C Spot net PnL relative to A.

Private reports:
`/home/julius/.local/state/aigorithmic-trading/reports/perp-intraday-timeframes-20261003-703days/`.
`comparison.md`/`comparison.json` contain all summaries, run IDs, deltas,
funding, costs, market/coin and long/short contributions, audit curve hashes
and separate fine Perp equity sidecars. Supplemental input/provenance root:
`/home/julius/.local/state/aigorithmic-trading/reports/perp-intraday-timeframes-20261003-703days-inputs/`.
New raw native acquisition root: `perp-intraday-native-20261003-703days/`
under that private reports directory's parent.

Result IDs:

- A: `710959d2e19bb7bbc0f33175ca72d269c89059384c532f9db54e45527ca5e412`
  — exact original H4/D1/H1 control ID, summary and complete3journals verified.
- B: `1d6a5ced89c3f52580752d76aa41c3827ab5152965d2af97cb10b036ac05fbf1`.
- C: `3c8f7ac8252ed1ddc8f204bb36ab149b86d4194eeed76c07aa94fe24b2320ba0`.

Evidence SHA256:

- Source SQLite unchanged:
  `63a099e3fdd67d9a46578d919578bc7d9231d753babc4dcf26326a02986efbb5`.
- Frozen base dataset:
  `1efd518e17072c05fb73772c970a78d0bdccdb13ea319c94b34e77374403132c`.
- Intraday input file:
  `0e3ba7289a0848e4f7e0234596f0f203107e04ac728a1f5ce0abc0515ae30a34`.
- Comparison JSON:
  `a703fa7893e73c77bb0a631c917c66efd35a3b542ce61ae364aa29805dd486f6`.
- Comparison Markdown:
  `315543885f7af6c600a6cb7c5128f7b3b1090ffea763c12feb9322c634b7b43c`.

Native H1/H4/H8/M15 contract and H4/M15 mark common active boundaries all
match exactly for BTC/ETH. No exception, normalization or synthetic H8/M15.
All3 summaries/full journals and common-grid audit reproduce offline;
source and original frozen parents remain unchanged.

Verification: a separate fresh offline process regenerated all3 result IDs,
summaries, full journals, fine Perp sidecars and passive audit hashes;
`independent-verification.json` records the receipt. Exact reused native
row subsets and retained acquisition metadata are additionally checked in
`lineage-verification.json`. Independent code review has no remaining
Critical/Required findings after regression-tested fixes for same-open
cost-trigger flatten, missing warmup reuse and zero-duration audit trades.
Full `uv run pytest -q`: **1,184 passed**, 29 existing dependency warnings.
`uv build` (wheel and source distribution) and `git diff --check` pass.
No dependency, runtime, worker, rule, gate, database, soak or account change;
local commits only, no push, merge or deployment.
