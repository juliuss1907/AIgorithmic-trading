# Buy-and-hold and Perp margin comparison

Completed local research, 2026-10-04. Frozen window: 2024-10-29 00:00 UTC
through 2026-10-02 00:00 UTC, 703 days (both boundaries 07:00 UTC+7).
Each case starts independently with 1,000 USDT. No API/LLM calls, runtime changes,
gate promotion, activation, push or deployment.

## Experiment contract

| Case | Spot | Perp | Reserve |
|---|---|---|---|
| Donchian + short1x | 600 initial notional budget; active rules | 300 margin; isolated1x, short-only | 100; flat-only restore/repay |
| Hold + long-short2x | 600 initial notional; fixed quantities | 300 margin; isolated2x, both directions | 100; no transfers |
| Hold-only | 100% cash including entry costs; fixed quantities | None | None |

Spot weights BTC/ETH/SOL/NEAR/ZEC: 40/20/20/10/10. Perp BTC/ETH margin weights:
50/50. Mixed Spot entry costs are paid in addition to the 600 notional budget;
the hold-only benchmark scales purchases down to include entry costs within 1,000.
Buy once at the first H4 open and sell at the final H4 close, with costs on both
ends; no periodic rebalance, sizing adjustment, stop, daily-risk sale or signal exit.

Active Spot preserves H4 Donchian30/8, closed D1 EMA50 trend filter, ATR14 sizing
and 10% emergency price stop. Both new Perp cases use closed H4 Donchian30/8,
symmetric closed D1 EMA50 direction/slope, ATR14 sizing and a fixed 10% price stop
(not 10% margin). No ATR stop, profit target or trailing. Signals fill next H4 open;
M15 contract candles drive Perp emergency stop detection, and M15 marks plus actual
funding/cost events drive Perp collateral/daily risk. Historical costs: Spot10bps
fee +5bps slippage, Perp5bps fee +5bps slippage per fill; actual funding charged once.

Perp daily loss3% uses its own marked day-start sleeve equity, excluding net
capital transfers. Parent daily loss3% is checked at H4/funding/cost events.
The active case can flatten Spot and Perp; the hold case blocks/flattens Perp
only, leaving held Spot untouched. Resume requires the next UTC day, flat Perps
and no fresh-day parent breach; held Spot need not be flat. Daily thresholds are
triggers, not guaranteed maximum realized loss (gap/next-open fills and costs).
DD10% is observe-only in all cases, **not a terminal halt**.

Sizing uses each sleeve's realized capital only. Unrealized PnL enters risk
valuation, not new sizing. Reserve transfers follow the existing
[flat-only restore/repay contract](perp-short-reserve-research.md#reserve-accounting):
restore Perp300, then repay reserve100. They conserve capital and are not profit;
they cannot rescue a live position or guarantee against liquidation.

## Verified results

Amounts in USDT, after modeled fees/slippage and complete actual funding.
DD is the passive common-M15 audit, with Spot valued at latest known H4 open/close.
It is not exact intrabar drawdown.

| Case | Final capital | Net profit | Return | Max portfolio DD | Perp net profit |
|---|---:|---:|---:|---:|---:|
| Donchian + short1x + reserve | 1,343.54 | +343.54 | +34.35% | 9.59% | -9.00 |
| Hold60 + long-short2x | 3,067.06 | +2,067.06 | +206.71% | 48.09% | +18.99 |
| Hold100-only | 4,408.33 | +3,408.33 | +340.83% | 60.72% | 0.00 |

All cases run the full 703 days. Short1x closes 62 Perp trades; gross reserve
draw195.34 and repay186.34 leave reserve91.00 and Perp300. Gross draws exceed100
because repayments can be reused. Perp flow-adjusted DD is34.47%.
The long-short case closes145 Perp trades: 76 longs net+172.58 and69 shorts
net-153.59; Perp DD49.80%. Each held coin closes exactly one round trip at window end.

Interpretation limits:

- Buy/hold100 invests more Spot capital than the mixed cases, so this is not
  a one-variable test proving that removing Perp creates the return difference.
- Hold100's ZEC contribution is+3,370.31 out of total+3,408.33 (about98.9%).
  BTC+83.84, ETH+10.19, NEAR+11.55 and SOL-67.56. The result is concentrated in
  one historical winner, not broad evidence of a robust diversified strategy.
- Short1x does not increase return over the prior active Spot600-only control
  (+35.25%, final1,352.54); it reduces observed portfolio DD from10.36% to9.59%.
- Hold60/long-short2x profits mostly from Spot (+2,048.07), not Perp (+18.99),
  and fails the descriptive DD<10% economic research check. Hold100 is a
  benchmark exempt from the minimum-trade strategy check, not a passing gate.
- Research uses fractional fills and fixed cost assumptions, not full exchange
  filters, partial fills or guaranteed liquidation behavior. Reused historical
  data and selected weights are not an out-of-sample profitability guarantee.

## Reproduction and evidence

```bash
uv run python -m intraday.replay_v2.hold_margin_study \
  --frozen-root /path/to/perp-short-reserve-20261004-703days \
  --report-root /path/to/new-private-report-directory
```

Requires the canonical prior frozen short-reserve study, three exported input
files and its unchanged original source backup. No downloading or source repair.
Existing output directories are refused, not overwritten.

Verified output:
`/home/julius/.local/state/aigorithmic-trading/reports/hold-margin-20261004-703days-verified/`.
Open `comparison.md` or `comparison.json`; individual private reports contain
summary, full trade/event/equity journals and manifests. Mixed cases additionally
have fine Perp-equity and reserve-transfer sidecars; all cases have common-grid audits.
The earlier output without the `-verified` suffix is preserved but incomplete
(benchmark publication failed), and should not be used as the finished comparison.

Both original short2x controls reproduce their original identities, summaries
and three journals. All three new cases are replayed twice from the same frozen
exports; publication verifies identities, summaries, journals, sidecars and audits.
Source database and exported input checksums remain unchanged.
Full regression: 1,210 tests passed; package build passed.


## Rerun after daily-loss fixes — 2026-10-06

Two ledger fixes change the evaluator: a parent daily-loss resume no longer
erases a loss taken on the new UTC day before the book is flat, and the parent
and Perp-sleeve UTC daily baselines now anchor at the 00:00 marks in every
engine (previously only the Donchian filter book did). The study was replayed
offline from the same frozen inputs (identical SHA256) into a new directory;
every variant passed the deterministic double replay. Old evidence is kept.

Evaluator `historical-hold-margin-research-v1.1`; report:
`/home/julius/.local/state/aigorithmic-trading/reports/hold-margin-20261006-rerun-v2/`.
The study checks its control against the frozen short-reserve root, whose
control identity changes with the new evaluator version, so `--frozen-root`
points at `perp-short-reserve-20261006-rerun/`; its three input files are
byte-identical to the original root. All three economic summaries are unchanged;
only the descriptive worst observed Perp day moves by less than 0.003 pp.
Full `uv run pytest -q`: **1,245 passed**.
