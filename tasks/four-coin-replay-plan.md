# Four-coin replay v2 and adaptive Perp confidence

Approved 2026-10-02. ETH, NEAR, ZEC, SOL only for this study. HYPE excluded
from research, not deleted/disabled. BTC and all live campaigns stay unchanged.
No push, deployment, promotion, exchange settings/orders or activation.

## Decisions

- Snapshot source consistently using SQLite backup; integrity/checksum manifest.
  Append downloaded native closed candles to research copy only.
- Spot: 24 calendar months; first 18 selection, final 6 holdout; insufficient
  history deferred. ETH existing/30-8; others existing/30-8/40-8/50-8, deduplicated.
  ATR14 for experiments, other filters unchanged. Select passing training rule by
  net return minus drawdown, then smaller drawdown. Never reselect on holdout.
- Perp: at most 90 days archived Jev decisions/quotes, 70/30 chronological split.
  LLM sees selection summaries only; per-coin confidence 69%-100% inclusive,
  finite fractional values, target floor70% with one percentage-point tolerance.
  No85% ceiling; no runtime subtraction of tolerance. Legacy hashes preserved.
- Only confidence changes for Perp; hard risk, stops, leverage and other filters
  stay fixed. Independent1000USDT accounts, isolated3x simulation.
- Regular-user fees: Spot10bps+5bps slippage/fill, Perp5bps+5bps slippage/fill,
  spread implicit, actual complete funding separately collected before simulation.
- Weekly Monday09:00 Asia/Ho_Chi_Minh; one model attempt/coin/week, >=100 valid
  training decisions, >=95%coverage, >=100 fresh verified decisions. Pending
  passing proposal blocks competing proposals. Default off; four-symbol opt-in.
- Research artifacts separate from registry. CLI study/review/status and read-only
  dashboard proposals, no Telegram or automatic apply. Explicit operator gate/soak
  remains separate; existing gate thresholds and >=14day fresh validation retained.

## Implementation sequence

Contracts/snapshot -> study runner -> confidence review -> CLI -> weekly scheduler
-> read-only dashboard -> data-backed batch and verification. Tests at each slice.
Report storage uses existing private replay root; timestamps UTC, displayUTC+7.

## Acceptance

All four routes report pass/reject/deferred without changing source registry,
collection anchors, BTC or live campaigns. Source provenance/funding gaps never
become fake zero-cost PnL. Spot AI filter remains explicitly unreplayed. Browser
and CLI expose proposed versus current values without granting trading authority.
