# Donchian historical holdout — completed 2026-10-05

Verdict: **Inconclusive**, not Pass and not Fail. G1/G2/G3 all pass; only 3/6
pre-registered hypotheses pass (minimum 4 for Pass). No strategy retuning,
automatic A1/A3 fallback, official gate update, VPS deployment or activation.

## Frozen setup and evidence

Active UTC window: [2022-01-01 00:00, 2024-10-28 20:00), about 1031.83 days.
BTC/ETH/SOL; initial 1000 USDT; Spot 60% / Short 40%, each BTC 40% / ETH 30% / SOL 30%;
short 1x; independent realized reinvestment; no reserve/transfers.
H4 Donchian 30/10 A4, EMA 200/50, Wilder ADX/DMI 14, preceding Volume MA20 >1.2x,
preceding 20-day M15 approximate volume profile. ATR14 simple TR x3 ratcheting
trailing; native M15 risk; combined UTC daily loss 3%, next-day AND flat resume;
DD observe-only, 15% scores G3. No Jev/LLM calls.

Engine: `e1d4918a7f5074fa2388614366828c27d7758273`.
Freeze06: `b2a479bd5035c15709a18295315ff4c47815158f5a06eed8ece95b899fe8ebf9`.
Strategy/criteria fingerprints unchanged from freeze01. Criteria:
6cb276d6b64145a1a3fa8d9fe85525aab3b9892b7657af86ba942034e8e36ed6.
Six old reference summaries/result IDs/full journal hashes reproduced exactly;
old seven-case reference verdict Pass. Each new case verified deterministically
against its complete journals; original input/source checksums preserved.
Evaluator verdict emitted FIRST, before fresh PnL inspection.

## Seven confirmation runs

| Case | Final USDT | Net USDT | Return % | Known DD % | Trades |
|---|---:|---:|---:|---:|---:|
| A0 30/10 | 1526.08 | 526.08 | 52.61 | 12.83 | 464 |
| A1 30/10 | 1565.18 | 565.18 | 56.52 | 12.39 | 390 |
| A2 30/10 | 1368.03 | 368.03 | 36.80 | 11.12 | 251 |
| A3 30/10 | 1377.21 | 377.21 | 37.72 | 11.02 | 233 |
| A4 30/10 primary | 1348.67 | 348.67 | 34.87 | 10.47 | 204 |
| A4 20/10 | 1330.68 | 330.68 | 33.07 | 11.75 | 215 |
| A4 30/10 fee/slippage x2 | 1249.23 | 249.23 | 24.92 | 11.35 | 204 |

Returns are for the entire window, not annual returns. Stress reruns the actual
ledger with doubled per-fill fee/slippage including sizing/cash effects;
funding rates are unchanged. It is not an arithmetic subtraction from base PnL.

## Locked scoring interpretation

- G1: primary net 348.67 >0; pass.
- G2: actual stress net 249.23 >0; pass.
- G3: known marked DD 10.47% <=15%; pass.
- H1: primary fee/slip cost is 0.4113x A0, below 0.6; pass.
- H2: ADX DD ratio A2/A1 =0.8970, above 0.75; fail. DD reduction about 10.30%,
  not the hypothesized minimum 25%.
- H3: primary 348.67 >= A4-20/10 330.68; pass.
- H4: ETH Spot 50.68 and Perp 30.10 are both positive; pass.
- H5: primary return/DD 3.3302 < EMA-only A1 4.5605; fail.
- H6: primary return/DD 3.3302 < A3 3.4225; fail.

The fuller filters reduce costs/trade count, but their claimed improvements in
risk-adjusted return and ADX drawdown reduction did not sufficiently repeat.
Do not select A1/A3 after seeing this holdout; no automatic fallback was locked.
Inconclusive permits an operator to consider prospective paper with the frozen
setup, not real-trading approval or proof of edge. Define prospective stop/
minimum-trade criteria before starting. No paper process was activated here.

## Primary accounting and descriptive checks

| Sleeve/coin | Trades | Net USDT |
|---|---:|---:|
| Spot BTC | 35 | 107.64 |
| Spot ETH | 30 | 50.68 |
| Spot SOL | 38 | 118.90 |
| Short BTC | 27 | 52.45 |
| Short ETH | 35 | 30.10 |
| Short SOL | 39 | -11.09 |

Primary fees 56.80, cash-charged slippage 34.39, net funding paid 4.23 USDT.
Buy-and-hold 100% Spot 40/30/30 from real first-open/last-close prices after both
fills' costs ends 1115.16 USDT (+11.52%), known close-sampled DD 76.57%.
Cash baseline 0% assumed yield; no invented stablecoin yield.
Annual marked equity changes: 2022 +7.08%, 2023 +27.14%, 2024 partial -0.94%.
Seeded 5000-draw synchronized daily block bootstrap 95% return intervals:
7-day [-5.50%, 98.12%], 14-day [-8.29%, 101.82%]. Both include loss; descriptive
conditional-history uncertainty, not independent/prospective proof.
Old+new primary: 344 closed trades, net 507.78 USDT across TWO independent 1000 USDT
accounts; no fictitious continuous compounded equity/DD/CAGR.

## Approved source assumptions and limitations

All approvals preceded fresh strategy result inspection. See
[recorded data policy](donchian-oos-data-repair-2026-10-05.md).

- Exactly 3 warmup H4 closeTime metadata repairs; OHLCV unchanged.
- 5 missing Spot M15 opens; actual partial source timestamps retained, no fake
  candles/fills. Pending exits defer to real reopening prices; stale labels.
- 1 missing native mark M15 bar; prior observed mark retained for 15 minutes,
  not interpolated. Independent native trade prices/funding continue.
- 2005 missing funding settlement-price quotes each for BTC and ETH, and 2080 for SOL,
  use actual native mark OPEN 0–31ms earlier. Rate/time/original raw rows unchanged;
  derived quote lineage verified. These are NOT API-confirmed settlement prices.
- Exactly 2 Spot open and 3 Perp close H4/M15 disagreements admitted with their
  original prices, no generic epsilon. H4 indicators/M15 fills keep their views.
- Primary has 13 stale Spot valuation samples and 2 stale mark samples. DD is
  known-price sampled, not exact tick/intrabar DD. Daily 3% cannot cap overshoot.
- OHLC touch fills, approximate volume profile, fractional sizing, slippage,
  partial fills/liquidation and retrospective holdout limitations remain.

## Artifacts and verification

Private artifact root:
`~/.local/state/aigorithmic-trading/reports/donchian-oos-2022-2024/`.

- `freeze-20261005-06.json`, `freeze-invariance-20261005-06.json`.
- `data-20261005-06/inputs.json`, QA/native snapshots and source hashes.
- `oos-20261005-06/comparison.json`, evaluation JSON/Markdown, seven manifests
  and full trades/events/equity journals (primary run d8d3ebc0b58143feac8bd0569a8a47c2).
- `analysis-20261005-06/report.md`, analysis JSON and verified manifest:
  coin/market/year/exits/event months, benchmarks, stress, bootstrap and pooling.
- Original source diagnostics, previous freezes/references/failed batches intact.

Full regression: 1294 passed; 1 pre-existing fresh-worktree failure from absent
ignored Hermes `.env.template`. No secrets copied or test skipped. Focused
research/boundary tests, independent scoped reviews, wheel/sdist build and
diff checks pass. No runtime SQLite/provider/gate/champion changes, push,
merge, deployment, model calls or orders. Worktree:
`feature/donchian-oos-2022-2024`; original checkout/user files preserved.
