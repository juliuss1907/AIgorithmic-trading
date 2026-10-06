# Setup 2 diagnostic findings — 2026-10-06

Completed the approved descriptive investigation of the existing419-trade
Setup-2 journal. No new strategy run, threshold search, filter selection, source
exception, deployment, or trading activation was introduced.

Frozen analysis commit `312e4f5e1ad42bcf5f106a451f11b1851b40cfb9`; independent
audit commit `ad38776344fe1f9253c8266cf925e4f390727ab3`. The source strategy
remains the original Setup-2 engine `0ca9bc9d8e24b7d82454d08df3ec049127980e07`.
The analysis source/config/comparison/journals were locked before calculating
group outcomes. Original18 publications and input hashes matched again afterward.

Private report root:
`state/research-reports/donchian-setup2-diagnostics-20261006/` under the original
system-trading workspace. The independent audit's `audit/report.md` escapes
literal pipe characters in group IDs so Markdown tables render correctly;
`report.md` preserves the original frozen publication. Numbers and bins match.
Both publications have their own checksum manifests.

Baseline:177wins/242losses,663.9825162550667USDT net,174.51370959896636USDT
costs, mean net R0.1481903613. Initial1R is quantity times the fill-to-initial-stop
price distance; costs are included in net R and excluded from its denominator.

Costs are76.77USDT on winners and97.75USDT on losers, with signed funding.
Of242net losers,235already have negative gross PnL;7gross winners become net
losers after costs. Mean winner13.15USDT and mean loser−6.87USDT explain why a
42.24% win rate can still be profitable.

Top10net-USDT winners contribute758.80USDT; the other409trades sum−94.82USDT.
Those409still average+0.01305net R, illustrating how position sizes affect
USDT attribution. Top10by net R instead leave−83.28USDT and+0.00551mean net R.
Top20by net USDT leave−422.57USDT and−0.05365mean net R. These are fixed-journal
sums, not simulations with winners removed or equity returns.

Exactly two groups meet the frozen review criterion: n≥20, negative overall
mean net R, plus at least3negative complete entry years2022–2025 with≥5trades
per qualifying year. Partial2026 cannot qualify. No criterion was relaxed.

| Group | Trades | Win/loss | Net USDT | Mean net R | Qualifying years | Global top10 USDT winners |
|---|---:|---:|---:|---:|---|---:|
| SOL Short, volume/VolumeMA20 in (1.2,2] | 31 | 10/21 | −72.76 | −0.17315 | 2022,2024,2025 | 0 |
| ZEC Spot, directional EMA50 distance >2ATR | 68 | 23/45 | +13.06 | −0.00554 | 2022,2023,2024 | 3 |

The SOL subgroup has10.64USDT costs and PF0.489. Qualifying year samples are
14,6,5trades; this meets the approved criterion with modest samples and does not
establish that excluding these trades would improve a reinvested replay.

The ZEC subgroup has28.11USDT costs and PF1.042. Its three global top10 winners
contribute217.31USDT. It covers68of70ZEC Spot trades; a filter against it could
remove most of that market's trades, including large winners. Positive USDT and
negative mean R must be interpreted together; it is not a demonstrated losing
USDT subgroup.2025andpartial2026have positive mean R.

All15portfolio feature bins and all6whole coin/market scopes have positive mean
net R when nonempty. The ATR>4% portfolio group has−19.79USDT but+0.00531mean
net R, so it does not qualify for review. EMA50 distance≤1ATR has no entries,
and(1,2]has only9entries portfolio-wide; this historical sample gives little
support for evaluating those low-distance bins. Bins remain unchanged.

Outputs include419enriched trade rows,112groups with overall/5year/10half-year
cohorts (1,792CSV rows), separate USDT/R winner rankings, and two static plots.
Year/half-year figures attribute full closed net to the entry period, not marked
equity change in that period. This is seen-data research, not fresh OOS evidence.

Validation:36focused tests pass, including existing descriptive-analysis
regression; compile and diff checks pass. The independent50-digit Decimal audit
matches raw entry events, risk/features, all memberships/cohorts, candidate
eligibility, winner ranks/flags, and every exported group CSV field. Full source
and18journal checksum checks pass. No broad runtime/web build was rerun for this
standalone report-only change; earlier Hermes/web sandbox limitations remain
documented in the prior study's checklist. The user checkout remains unchanged.

See [the diagnostic rules and command](donchian-setup2-diagnostics.md). Operating
backlog is preserved. Candidate groups are hypotheses for a separately locked
experiment, with no automatic filter adoption or trading approval.
