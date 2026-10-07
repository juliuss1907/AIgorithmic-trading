# Frozen Donchian historical holdout research

Isolated local research. No VPS, gates, champions, models, credentials, orders,
push, merge or deployment. Preserve all previous research and soak evidence.

Window: [2022-01-01T00:00:00Z, 2024-10-28T20:00:00Z).
Warmup: exactly 600 native H4 bars and 1936 native trade M15 bars.
BTC/ETH/SOL native Binance Spot, USD-M trade/mark candles and actual funding.
1000 USDT; Spot60/Short40; both sleeves BTC40/ETH30/SOL30; short1x;
independent realized reinvestment, no reserve/transfers. Parent UTC daily3%,
ATR14 simple TR x3 closed-H4 trailing checked M15, Donchian exit retained.
DD observe-only; 15% is a research verdict criterion, not a trading halt.

Seven runs: A0–A4 Donchian30/10, A4-20/10, A4-30/10 costs x2.
Base per-fill Spot fee/slip10/5bps; Perp5/5bps. Stress doubles both,
including sizing and fills; actual funding is unchanged, never statically deducted.

1. Implement/test instance costs, criteria/evaluator and immutable runner.
2. Verify six old golden ledgers and actual old-window stress rerun.
3. Review, regression, build; commit and seal engine/lockfile/criteria/strategy.
4. Collect fresh data only after seal. Validate gaps, duplicates, finite OHLC,
   boundaries and funding <=8h+1s. Flag zero volume/basis; no interpolation.
5. Execute once plus mechanical deterministic verification; evaluator verdict
   precedes result inspection. Resume only identical sealed inputs/configs.
6. Describe coin/market/year/exits/events, cost stress, buy-and-hold and cash,
   daily block bootstrap and separate-window pooled trade statistics.

Rules: all G1-G3 plus >=4 H => Pass; all G but <4 H => Inconclusive;
any G fails => Fail; missing/invalid evidence => Invalid. No automatic A3
fallback or activation. Pass/Inconclusive can support an operator decision
to start prospective paper; neither proves edge. New variants need new data.

See docs/donchian-oos-criteria.json for locked machine criteria and
docs/donchian-oos-criteria.md for interpretation. Outputs are exclusive new
batch folders beneath ~/.local/state/aigorithmic-trading/reports/donchian-oos-2022-2024/.

## Execution status — 2026-10-05

- Approved H4 warmup timestamp correction and source-preserving Spot M15 gap
  policy implemented; see docs/donchian-oos-data-repair-2026-10-05.md.
- Engine cf77696, freeze03 checksum d71b3136899f2f25c4c0691c5b4f9804c5b5941474669cc823215201b51b888f.
  Strategy and criteria fingerprints unchanged. Independent scoped review found
  no Critical/Required defects.67 focused tests passed; build and diff checks pass.
  Full suite1280 passed,1 unrelated baseline failure: missing ignored Hermes
  `.env.template` in this worktree. No credentials copied or test skipped.
- Reference03 reproduced all six legacy result IDs/summaries/full journals.
  All seven reference cases mechanically deterministic; reference verdict Pass.
  This is OLD-window validation, not a fresh OOS verdict.
- Data03 retains approved Spot evidence and validated BTC Perp trade H4/M15.
  Collection stopped at BTC mark M15 strict coverage validation. A separate
  same-endpoint audit checked all three mark histories: each has99055 rows,
  no duplicates/irregular closes, and exactly one missing open at
  2023-11-10T03:45UTC (10:45UTC+7). Exact-window retries return empty for all3.
- Raw evidence:`diagnostics-mark-20261005-03/native-mark-audit.json` with per-coin
  original file SHA256. No mark correction/substitution accepted. Data03 has no
  completed QA/inputs bundle and no fresh strategy results. Previous failed
  batches/freezes/raw/reference remain intact. Operator policy decision requested:
  seek original Binance mark bar elsewhere first, or explicitly permit stale
  last-observed mark valuation for this15-minute source gap. Never auto-extend
  Spot execution availability policy to mark/Perp/funding.
- No push, merge, deployment, model calls or trading activation performed.

## Final completion — supersedes the earlier collection blockers

All separately approved source policies are implemented and recorded before
fresh PnL inspection: Spot availability, mark stale interval, funding native-open
references and exactly 5 native boundary price-view discrepancies. No original
price/rate/time data rewritten or synthetic bars; strict defaults unchanged.
Engine e1d4918, freeze06 b2a479bd5035c15709a18295315ff4c47815158f5a06eed8ece95b899fe8ebf9.
Strategy and machine criteria still match freeze01. Reference06 reproduces all
six legacy full journals; all seven new runs deterministic, immutable and scored.

Completed new verdict: **Inconclusive**. G1/G2/G3 pass; H1/H3/H4 pass (3/6).
Primary net 348.67 USDT, final 1348.67, +34.87%, known DD 10.47%, 204 trades.
Fee/slippage x2 actual ledger: net 249.23, final 1249.23, +24.92%, DD 11.35%.
No retuning, automatic fallback or activation. Operator may consider prospective
paper with frozen rules; new variants require new evidence, not reuse of OOS.

Full report: [completed results](../docs/donchian-oos-results-2026-10-05.md).
Private `analysis-20261005-06/report.md` includes annual/coin/market/event/exit,
benchmarks, bootstrap and separate-account pooling. Report/source/seal manifests
verified. Full regression: 1294 passed, 1 unchanged Hermes template baseline failure;
build and scoped reviews pass. Original raw/failed batches/checkouts preserved.
No push/merge/VPS/runtime/gate/champion/provider/model/order changes.
