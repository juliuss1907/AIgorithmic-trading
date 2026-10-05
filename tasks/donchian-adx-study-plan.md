# A4 ADX sensitivity research — accepted 2026-10-05

Local research only; no VPS, orders, models, activation, gate/champion writes,
push or merge. Preserve the frozen OOS worktree and original source artifacts.

- [ ] Parameterize A4 ADX without changing legacy config/defaults.
- [ ] Join immutable data for [2022-01-01, 2026-10-02) UTC, verify overlaps,
      collect only the missing 4-hour native mark interval and bind funding lineage.
- [ ] Admit only the three approved Perp open differences at 2024-10-28T20:00UTC;
      retain the five older approved differences and source availability policies.
- [ ] Verify legacy journals, tests and build; freeze source/config/data checksums.
- [ ] Run five cases plus mechanical full-journal deterministic verification.
- [ ] Publish annual/half-year/coin/market and blocked-signal comparisons.

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
