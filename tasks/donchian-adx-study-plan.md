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
- [ ] Complete strict native QA for new Spot H4/M15 and mark M15.
- [ ] Replay five cases twice and publish all journals, costs, daily stops and DD recovery.
- [ ] Publish annual/half-year and ten coin/market contributions, blocked-off-trade
      attribution and three-coin comparison with the allocation caveat.
- [ ] Reverify original three-coin IDs/configs/summary/methodology and full journals.
- [ ] Complete regression/build and report the existing Hermes template failure separately.

New source anomalies block strategy replay. Acquisition and rejected original rows
are retained under private `reports/donchian-adx-five-20261005/`; no source repair,
new gap policy or native price tolerance is admitted without separate approval.
Full raw auditing continues independently to make that approval concrete.
Research results remain pending; ADX20 is a preferred candidate, not a selected winner.
