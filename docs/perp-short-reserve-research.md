# Short-only Perp and reserve research

Local deterministic preset `perp-short-reserve`. Research only: no changes to
runtime signals, Jev/LLM, confidence, gates, workers, soak, Binance Demo/real,
account connection or activation. No automatic promotion, push or deployment.

## Locked experiment contract

Default frozen window: **2024-10-29 00:00 UTC → 2026-10-02 00:00 UTC**, 703 days
(both boundaries 07:00 UTC+7). Initial capital1,000USDT; restarted independently.

| Case | Initial Spot | Initial Perp allocation | Leverage/direction | Reserve policy |
|---|---:|---|---|---|
| Spot-only | 600 | 300 unused, plus100 unused | None | None |
| Original A | 600 | 300 **notional** budget | Isolated3x, long/short | No transfers |
| Short2x off | 600 | 300 **margin** budget | Isolated2x, short-only | No transfers |
| Short2x restore/repay | 600 | 300 **margin** budget | Isolated2x, short-only | Flat-only restore/repay |

Spot BTC/ETH/SOL/NEAR/ZEC weights40/20/20/10/10; Perp BTC/ETH50/50.
Separate own realized budgets: no unrealized PnL sizing, Spot subsidy or daily
full rebalance. Locked entry margin does not shrink with falling short marks.
New cases can request up to600 gross Perp notional initially, before ATR sizing,
entry costs and shared cash constraints. Actual remaining reserve is protected;
the old combined90% notional cap and permanent10% equity reserve do not apply.

Spot keeps H4 Donchian30/8, D1 EMA50 rising/below-close trend filter,
ATR14 simple mean TR sizing and10% emergency price stop. Perp mirrors it:
short when closed H4 close<previous30 lows, with closed D1 close<EMA50 and
falling EMA; exit when closed H4 close>previous8 highs. Signals/Donchian exits
fill at next H4 open. ATR multiplier is `min(1, .02/ATR_pct)` for positive ATR,
otherwise zero; Perp applies it to per-coin margin before multiplying by2.
ATR is a size input, **not** an ATR stop in the new cases.

Short emergency stop is entry price×1.10, not10% margin. Native M15 contract
open gaps fill at open; high touches fill at stop, detected at M15 close.
No ATR×3 stop, trailing, fixed profit cap, DCA or averaging down. Original A
retains its old H4/D1, ATR14×3 stop and H1 net trailing exactly.

Perp marked/collateral risk uses native M15 opens/closes, actual funding and
fill-cost events. Parent risk and Spot remain H4 plus funding/cost events,
not periodic M15 parent evaluation. Daily loss is3% of own marked Perp
day-start equity and3% of portfolio day-start equity. At opens: parent →
Perp daily → emergency gap → H4 Donchian → entries. Close/funding daily triggers
flatten next M15 open; open/cost triggers flatten at the same open.
UTC daily locks require next day AND Perp flat AND parent safe to resume.
DD10% is **observe-only**, not a terminal halt; economic research check DD<10%
remains. Insolvency/unsupported collateral safeguards remain limitations.

## Reserve accounting

`P0=300`, `R0=100`; `P` is actual realized Perp capital, `R` remaining reserve.
Only after **all** Perps are flat, following exit batch and risk evaluation:

- If `P<P0`, draw `min(P0-P,R)` reserve→Perp.
- If `P>P0`, repay `min(P-P0,R0-R)` Perp→reserve.
- Restore Perp300 before repaying reserve100. After reserve100 is restored,
  remaining Perp profits compound in Perp. Empty reserve cannot borrow Spot.
- Never transfer into an open position or because of forced `window_end` exits.
- Both new policies prohibit Perp entry at the same flat-batch timestamp;
  only a later eligible H4 open can enter. Exit-close carry also blocks the
  same symbol at its immediately following open.

Example285/100 → draw15 →300/85; later308/85 → repay8 →300/93.
Transfers conserve global cash/equity and are **not** PnL. Entry fees/slippage,
exit costs and funding charge once. Flows never reset daily locks or peaks.
Daily return excludes net inflows today, using actual funded day-start equity;
the next UTC day starts from actual funded marked balance. Cumulative Perp
performance equity excludes all net capital flows for DD/statistics.

End-flat reconciliation:

```text
Perp capital = 300 + Perp net trading PnL + drawn - repaid
Reserve      = 100 - drawn + repaid
Spot capital = 600 + Spot net trading PnL
Sum          = global cash = 1000 + all net trading PnL
```

Reserve always0..100; cumulative gross draw may exceed100 through reuse of
repayments. Decimal fractional accounting reconciliation tolerance1e-18USDT.

## Offline command and artifacts

```bash
uv run python -m intraday.replay_v2.mixed_portfolio_research \
  --mode historical-quant --preset perp-short-reserve \
  --database /path/to/verified-immutable-backup.sqlite3 \
  --perp-inputs /path/to/frozen-703days/perp-inputs.json \
  --intraday-inputs /path/to/frozen-native/intraday-inputs.json \
  --baseline-reference /path/to/original-A/reports/RUN_ID \
  --report-root /path/to/new-private-directory
```

Frozen-only: no collection/reuse flags or API keys. Consistent aware
`--start`/`--end` can narrow the window; only observe-only DD is accepted.
Never overwrite reports. Source backup manifest, source/identity/coverage,
native common boundaries, finite OHLC, missing/duplicate bars and all checksums
remain strictly validated. No synthetic H8/M15 or silent discrepancy repair.

Each variant publishes summary and full equity/trade/event journals. New
variants additionally publish fine Perp equity and reserve-transfer sidecars.
All4 have passive common-M15 audit curves using native Perp marks and H4 Spot
as-of prices; audits never feed execution. Perp audit DD excludes capital
flows, while actual allocated equity is displayed separately. Post-event
audit need not retain a transient pre-flatten risk trigger. No exact intrabar
path, instrument filters, partial fills or liquidation guarantee is modeled.
Known slippage/cost assumptions and ex-post research limitations remain.

## Completed result — 2026-10-04

All4 finish the full703-day window, with actual complete funding where traded.
Return is cumulative over this window, not daily or annualized, after costs.

| Case | Final USDT | Portfolio return | Spot net USDT | Perp net USDT | Common-M15 portfolio DD | Perp trades |
|---|---:|---:|---:|---:|---:|---:|
| Spot-only | 1,352.5432 | +35.2543% | +352.5432 | 0 | 10.3553% | 0 |
| Original A | 1,419.6712 | +41.9671% | +381.7541 | +37.9171 | 13.0313% | 148 |
| Short2x off | 1,231.0425 | +23.1042% | +352.5432 | −121.5007 | 16.6169% | 79 |
| Short2x restore/repay | 1,205.5472 | +20.5547% | +352.5432 | −146.9960 | 18.9880% | 79 |

The tested short strategy does **not** improve profit or portfolio DD.
Without replenishment Perp sizes shrink with losses; replenishment maintains
larger budgets for a losing strategy. Both new cases have37 Perp daily loss
pauses, no parent daily pause, and zero10% emergency stop exits. Daily guards
can close earlier than price stops. Neither loss threshold guarantees exact
maximum loss: marks/contracts, gaps and exit costs can overshoot. Worst
observed Perp day in the replenished case is−9.7328%, despite3% trigger.

Restore/repay makes21 transfers: gross draw145.6968, repayment45.6968,
net debt100, remaining reserve0. Actual Perp capital253.0040 includes100 net
reserve contribution; performance equity is153.0040, not a trading gain.
Without reserve transfers, Perp capital178.4993 and reserve100 remain.
Perp flow-neutral DD is51.3787% off versus63.9530% restore/repay.
New Perp gross price PnL is already negative (−93.8908/−110.5947), not just
fee drag; fee+slippage29.8661/39.2177, signed funding−2.2562/−2.8164 (received).

All4 reject the unchanged DD<10% research check. This is not a gate pass or
deployment recommendation. Spot keeps172 trades and identical Spot PnL across
Spot-only and both new cases on this dataset; shared constraints can change
Spot fills on another dataset.

Private artifact root:
`/home/julius/.local/state/aigorithmic-trading/reports/perp-short-reserve-20261004-703days/`.
`comparison.json` holds full summaries, market/coin PnL, fees/funding,
exposure/margin, daily pauses, reserve accounting, IDs and all sidecar hashes.
`independent-verification.json` confirms a fresh frozen-only process reproduced
all4 IDs/summaries, all3 journals and every risk/transfer/audit sidecar.

Original A ID/summary/all3 journals remain exact:
`710959d2e19bb7bbc0f33175ca72d269c89059384c532f9db54e45527ca5e412`.
Spot control ID: `441297b2bcccce2a49cc7f6c3dfabc0f35f7bceda57b67e678bbc95d9b6ec694`.
Short off ID: `c4a9dc65df10a10c5a8241227411200d596cb8cc1fcb9630dba9af22c6c5b789`.
Restore/repay ID: `a2a218204bbee35694d3091e007d59d07d937a4454be57dc74e8fb618e38bb80`.

Unchanged evidence SHA256:

- Source SQLite: `63a099e3fdd67d9a46578d919578bc7d9231d753babc4dcf26326a02986efbb5`.
- Frozen shared base dataset: `1efd518e17072c05fb73772c970a78d0bdccdb13ea319c94b34e77374403132c`.
- Original/exported Perp input file: `392465eab360494b915af03828182ea60d019bdae5d90aff37998aa1bcab257b`.
- Original/exported native file: `0e3ba7289a0848e4f7e0234596f0f203107e04ac728a1f5ce0abc0515ae30a34`.
- Comparison JSON: `c97ed7d80a85c8b997478361b496ae10735e5a9904ffac641c691d57ba238065`.

Independent code review: Required allocation-validation and flow-neutral
flat-event fixes have regression tests; no unresolved Critical/Required
findings. Previous preset defaults, evaluator hashes and journals unchanged.
Verification: `uv run pytest -q` **1,205 passed**,29 existing dependency
warnings;21 new focused tests. `uv build` wheel/source distribution,
research-link checks, CLI help and `git diff --check` pass. Local commits only;
no dependency, runtime, worker, source database, gate, soak or account changes.


## Rerun after daily-loss fixes — 2026-10-06

Two ledger fixes change the evaluator: a parent daily-loss resume no longer
erases a loss taken on the new UTC day before the book is flat, and the parent
and Perp-sleeve UTC daily baselines now anchor at the 00:00 marks in every
engine (previously only the Donchian filter book did). The study was replayed
offline from the same frozen inputs (identical SHA256) into a new directory;
every variant passed the deterministic double replay. Old evidence is kept.

Evaluator `historical-perp-short-reserve-v1.1`; report:
`/home/julius/.local/state/aigorithmic-trading/reports/perp-short-reserve-20261006-rerun/`.
All four economic summaries (final equity, returns, DD, trades, pauses, reserve
flows) are unchanged. Only descriptive Perp statistics move: worst observed day
−4.7169%→−4.7181% for A and −9.7304%→−9.7328% for both Short2x cases (updated
above). Full `uv run pytest -q`: **1,245 passed**.
