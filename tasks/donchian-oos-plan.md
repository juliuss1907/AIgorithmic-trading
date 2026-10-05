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
