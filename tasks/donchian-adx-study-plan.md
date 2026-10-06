# A4 ADX sensitivity research — accepted 2026-10-05

Local research only; no VPS, orders, models, activation, gate/champion writes,
push or merge. Preserve the frozen OOS worktree and original source artifacts.

- [x] Parameterize A4 ADX without changing legacy config/defaults.
- [x] Join immutable data for [2022-01-01, 2026-10-02) UTC, verify overlaps,
      collect only the missing 4-hour native mark interval and bind funding lineage.
- [x] Admit only the three approved Perp open differences at 2024-10-28T20:00UTC;
      retain the five older approved differences and source availability policies.
- [x] Verify legacy journals, tests and build; freeze source/config/data checksums.
- [x] Run five cases plus mechanical full-journal deterministic verification.
- [x] Publish annual/half-year/coin/market and blocked-signal comparisons.

Five cases: ADX/DMI disabled; ADX >20, >18, >15, >25 (control).
Enabled cases retain rising ADX and correct DMI direction. All cases retain
Donchian30/10, EMA200/50 H4, preceding VolumeMA20x1.2, Volume Profile,
ATR14x3 stops/trailing and existing ATR sizing. Initial1000USDT; Spot60/Short40,
BTC40/ETH30/SOL30 each; Short1x; independent realized reinvestment, no transfers.
Parent UTC daily3%; DD observe-only; no state reset at the data join.

This is exploratory research on already-seen data, not a new OOS gate.
Native original prices remain unchanged. Additional unapproved gaps/conflicts
stop collection before fresh strategy results. New artifacts go under
~/.local/state/aigorithmic-trading/reports/donchian-adx-2022-2026/.

Verification before fresh replay: six legacy cases match summary/result IDs and
all three full journals exactly. Full suite:1320 passed, one unrelated existing
Hermes test fails because ignored `.env.template` is absent in this worktree.
Wheel/sdist build passes. Original OOS source/criteria seal remains intact.

Joined input229387303bytes exceeds the legacy200MB reader limit. A bounded300MB
reader is scoped to this research only; legacy readers and trading logic remain
unchanged. This was fixed before any fresh strategy replay completed.

Completed at frozen research source commit `56eea87`. Published five-case
receipt: `donchian-adx-2022-2026/study-20261005-02/runs/comparison.json`.
Full report: `donchian-adx-2022-2026/analysis-20261005-01/report.md` under the
local private reports root. All cases complete, one dataset, ten successful
simulations including mechanical repeats; original source checksums unchanged.

The first publishing attempt stopped on integer-vs-string source-metadata
keys. Its output remains intact. JSON metadata canonicalization fixed this
without changing journals; the recovered ADX-off case matches all three
previous published journals byte-for-byte. Attempt and retry-verification
receipts record both infrastructure fixes. No research thresholds were changed.

NetUSDT/DD: off662.34/14.24%; ADX20 709.09/13.20%; ADX18 691.91/12.60%;
ADX15 624.22/14.75%; controlADX25 561.29/10.47%. No winner activated.

## Five-coin extension — 2026-10-05

Opt-in `--universe five`: BTC40/ETH20/SOL20/NEAR10/ZEC10 in each sleeve;
same continuous [2022-01-01,2026-10-02) UTC window and five ADX cases.
ETH/SOL decrease30%→20%; differences cannot be attributed entirely to new coins.

- [x] Add locked profile, serialization/resume and manifest references with SHA256.
- [x] Scope sparse-series handling to each symbol/series; new coins reject old whitelists.
- [x] Retain all native NEAR/ZEC Perp H4/M15 and original funding-rate evidence.
- [x] Complete strict native QA for new Spot H4/M15 and mark M15 under separately approved exact policy.
- [x] Replay five cases twice and publish all journals, costs, daily stops and DD recovery.
- [x] Publish annual/half-year and ten coin/market contributions, blocked-off-trade
      attribution and three-coin comparison with the allocation caveat.
- [x] Reverify original three-coin IDs/configs/summary/methodology and full journals.
- [x] Complete regression/build and report the existing Hermes template failure separately.

New source anomalies block strategy replay. Acquisition and rejected original rows
are retained under private `reports/donchian-adx-five-20261005/`; no source repair,
new gap policy or native price tolerance is admitted without separate approval.
Full raw auditing made that approval concrete before any fresh replay.
ADX20 is a preferred candidate, not a selected or activated winner.

Implementation commit `1e387e3`. All five original cases reproduced their IDs,
config checksums, summary, methodology and all three full journals. Receipt:
`reports/donchian-adx-five-20261005/legacy-verification/verification.json`.
Regression:1332 passed, one existing Hermes failure from absent ignored
`integrations/hermes/trading-ops/.env.template`; wheel/sdist build passed.
Full raw source audits retain all requested bars except the explicitly enumerated
native gaps. Review `data-approval.md` and `data-audit.json` in that private root
for the exact NEAR/ZEC policy. This initial implementation verification preceded
the approval and five-coin historical replays described below.

The user explicitly approved the exact listed source exceptions in-session.
`source-approval.json` binds that reply to `data-approval.md`, `data-audit.json`
and the separately scoped `approved-near-zec-native-source-20261005-v1` policy.
`approved-data/inputs.json` is a validated manifest; original raw rows remain
unchanged, including the two H4 closeTime values whose derived interval views
are normalized. Rates/times, prices and missing-bar evidence remain original.

Completed at frozen engine commit `1c6bcab`:5cases/10successful simulations,
each matching summary, methodology and all full journals on its mechanical repeat.
Same dataset checksum and unchanged sources; marked curve DD and trade/cost/net
accounting independently reconcile. All5old3coin cases matched again after the
new policy. Final regression:1343pass, the same one known Hermes failure; build pass.

Private completed evidence: `study-01/runs/comparison.json`, `analysis-01/report.md`,
`final-report.md`, `equity-drawdown.png`, `final-verification.json` and
`legacy-verification-final/verification.json` in the new report root.
FinalUSDT/return/DD: off1665.51/66.55%/10.01%; ADX20 1708.80/70.88%/11.46%;
ADX18 1680.12/68.01%/10.20%; ADX15 1611.74/61.17%/10.31%; ADX25 1506.05/50.61%/9.35%.
NEAR and ZEC are net-positive in every case. ADX20 has the highest return and
highest measured DD; new net contributions109.57/45.02USDT. Its final capital
is0.29USDT below3coin, while DD falls13.20%→11.46%. ETH/SOL reweighting remains
a confounder; no standalone-coin, extra-threshold, OOS or activation claim.

## Additional allocation/ADX samples — 2026-10-05

Five exact user-requested setups are opt-in through `--universe setups`;
see [the locked setup specification](../docs/donchian-adx-setups.md).
Same continuous 1,000USDT window/data/costs/risk rules. Setup1's650/350 groups
retain shared realized reinvestment within Spot/Short, explicitly confirmed by
the user. Setup3 is Spot100%, SOL/ZEC/NEAR4:3:3 as in setup2.

- [x] Lock the five allocations and per-market/per-coin ADX maps; no additional thresholds.
- [x] Test disjoint Spot/Short universes, Spot-only zero margin/funding, serialization/resume,
      threshold routing and trade/capital reconciliation.
- [x] Freeze clean source commit `0ca9bc9` before replay; reference unchanged approved5coin manifest.
- [x] Run each setup twice and match summary/methodology/result ID/all journals.
- [x] Publish wins/losses, costs, daily stops, DD recovery, yearly/half-year and coin×market tables,
      plus a CSV of every executed sample trade.
- [x] Verify all five prior3coin and all five prior5coin cases against their full publications.
- [x] Regression1352pass;1known Hermes template failure; wheel/sdist build pass.
- [x] Verify final source/manifest/accounting and commit delivery locally.

Evidence root: private `reports/donchian-adx-setups-20261005/`. Existing research
artifacts and operating backlog are preserved. This remains already-seen
historical research; no OOS/champion/activation claim, push, merge, deploy or LLM calls.

All10previous cases reproduced their full immutable reports at source commit0ca9bc9.
Receipts: `legacy-three-final/verification.json` and `legacy-five-final/verification.json`.
New setup finalUSDT/return/DD:1 1564.75/56.48%/9.48%;2 1663.98/66.40%/10.85%;
3 1669.19/66.92%/19.45%;4 1497.05/49.71%/12.45%;5 1468.59/46.86%/21.37%.
All1918executed entries passed their exact mapped thresholds and retained filters;
`entry-verification.json` records this check. Setup3 has14daily stops versus0in setup2,
with only5.21USDT additional profit. No new setup exceeds prior5coinADX20return70.88%.

Final accounting checks reconcile every trade group, fee/slippage/funding total,
yearly/half-year change and full marked-curve DD. All source checksums remain
unchanged. Completed artifacts: `final-report.md`, `equity-drawdown.png`,
`final-verification.json` and `final-manifest.json` in the new private root.

## Setup 2 isolated coin accounts — 2026-10-06

The user explicitly requested standalone SOL/ZEC/NEAR, superseding the older
no-standalone scope, and confirmed1,000USDT per independent account. Same window,
ADX20, Spot60/Short40 and approved data; total initial capital3,000USDT.
See [the isolated-account specification](../docs/donchian-adx-single.md).

- [x] Add locked opt-in profile and test selected-coin accounting, serialization/resume.
- [x] Add verified costs/outcomes/holding-time/annual/half-year and Setup-2 attribution reports.
- [x] Freeze clean source/config/input checksums before replay.
- [x] Reproduce original Setup-2 IDs, summary, methodology and all full journals.
- [x] Run three accounts twice and publish all journals and sample trades.
- [x] Complete independent accounting/source checks, available regression and build;
      record the sandbox web-test blocker separately.
- [x] Publish verified results and commit delivery locally; preserve operating backlog.

Frozen engine `c772c53887972ca16344481e6283514731014bf8`; original manifest and
prepared dataset checksums unchanged. All15prior profile config IDs remain
unchanged; Setup-2 reproduces its full publication. Six new simulations match
result IDs, summary, methodology and all journals. Independent checks reconcile
all421entries/trades, costs, funding, capital, full-curve DD and calendar periods.

SOL final1546.70USDT/return54.67%/DD21.70%/149trades/60wins/89losses/13daily stops.
ZEC final1182.28USDT/return18.23%/DD22.21%/135trades/55wins/80losses/10daily stops.
NEAR final1974.86USDT/return97.49%/DD20.68%/137trades/64wins/73losses/4daily stops.
These are three separate1,000USDT accounts, not one1,000USDT portfolio.

Regression1287pass,1known Hermes template failure;83tests in12TestClient files
remain unverified because a minimal empty FastAPI app also hangs in this sandbox.
The full-suite attempt and minimal diagnostic are preserved; wheel/sdist build
passes. No unrelated framework/environment fix was introduced.

The managed filesystem changed mid-task, making the old research worktree and
private reports directory read-only. Persistent artifacts therefore live under
workspace `state/research-reports/donchian-adx-single-20261006/`; an independent
local checkout and engine Git bundle preserve source commits. Initial edits in
the now-read-only research worktree remain documented in `execution-note.json`.
Original user checkout tracked files and operating backlog remain unchanged.

## Setup 2 trade diagnostics — 2026-10-06

Descriptive-only follow-up of419existing trades: SOL/ZEC/NEAR4:3:3,
ADX20, Spot60/Short40, same continuous1,000USDT account and window.
See [locked diagnostic rules](../docs/donchian-setup2-diagnostics.md) and
[completed findings](../docs/donchian-setup2-diagnostic-results.md).

- [x] Freeze analysis commit, five one-variable bin definitions, review criterion,
      source comparison/config/dataset checksums and all full journals before outcomes.
- [x] Require419unique causal entry-feature joins and positive nominal initial1R;
      reconcile177wins/242losses,663.9825net and174.5137costs.
- [x] Publish portfolio/six-market groups, entry-year/half-year cohorts,
      signed funding, costs, mean net R/PF and global top10-winner overlap.
- [x] Publish separate net-USDT/net-R top1/5/10/20rankings and fixed-journal
      remainder sums, explicitly without counterfactual-replay claims.
- [x] Apply n≥20 and≥3negative complete2022–2025entry years with≥5trades;
      retain empty/low-sample bins and exclude partial2026from eligibility.
- [x] Independently audit all419trade rows,112groups/1,792CSV rows and tails;
     36focused tests pass; source18reports and strategy code remain unchanged.
- [x] Preserve original publication and publish corrected Markdown rendering,
      local source commits, checksum manifests, and private charts/CSV/JSON.

Analysis freeze `312e4f5`; independent audit `ad38776`. Private artifacts:
workspace `state/research-reports/donchian-setup2-diagnostics-20261006/`.
SOL Short volume ratio(1.2,2]is−72.76USDT/−0.173mean R over31trades;
ZEC Spot directional EMA50 distance>2ATR is+13.06USDT/−0.00554mean R over68trades,
including3global top10USDT winners. Both meet the predefined review criterion;
neither is automatically selected for exclusion. Top10winners sum758.80USDT,
leaving−94.82USDT in409other fixed-journal trades. Only7gross winners become
net losers after costs. No new strategy runs, threshold changes, OOS claim,
push, merge, deploy, LLM calls or activation. Operating backlog stays intact.
