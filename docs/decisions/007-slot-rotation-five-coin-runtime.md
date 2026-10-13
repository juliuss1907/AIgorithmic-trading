# ADR-007: Slot-rotation design on five coins for the Binance Demo runtime

## Status

Accepted 2026-10-13 by the operator. Builds on [ADR-004](004-three-coin-system.md) and
[ADR-006](006-three-coin-runtime.md); changes the coin limit, the Spot/Perp split and the sizing
**for the new `setup2_slots_v1` profile only**. ADR-005 (the frozen ETH-NEAR-SOL prospective
test, rule hash `bf6fccc9…`) and the `setup2_v1` profile are untouched. The runtime is built
before any prospective verdict of this design, which ADR-004 requires to be recorded.

## Context

Several research rounds on already-seen data
([rotating-positions-research](../rotating-positions-research.md)) measured a design that differs
from the frozen basket: no per-coin weights, at most three open positions, a new signal that may
rotate out the weakest open position, separate Spot and Perp sleeves, an open-book PnL gate and a
5% stop. On ETH-NEAR-SOL-ZEC (ZEC standing in for HYPE), 2022-01-01 → 2026-10-02, entry window 25,
exit 10, one position at most a third of its sleeve, three positions:

| Case | Return | Max drawdown | Trades | Cost ×2 |
|---|---:|---:|---:|---:|
| Full window | +103,3% | 9,8% | 553 | +71,0% |
| 2026 only | +22,3% | 9,5% | 83 | +18,9% |

Reference points: ADR-005 `H4` +81,7% / 11,6% (3 coins), and the same design at entry 30
+88,7% / 9,4%. The operator chose entry 25 and a fifth coin (HYPE) and asked for the runtime.

## Decision

- **Profile `setup2_slots_v1`:** closed-H4 Donchian **25/10**, the A4 filters and ADX20 of Setup-2
  unchanged, ATR14×3 trailing stop **capped at 5%** from entry (never loosened). Fixed, never
  model-tuned; locked in the rule contracts like `setup2_v1`.
- **Capital:** two sleeves, Spot 50% and Perp 50% (Spot long and Perp short 1x isolated, as before).
  A position uses at most **one third of its sleeve's realized capital** times the usual ATR
  multiplier `min(1, 0,02/ATR%)`, bounded by the sleeve's free principal. Realized profit and loss
  (net of fees, slippage and funding) is reinvested per sleeve; unrealized profit is not.
- **Coins:** up to **five** active coins (ETH, NEAR, SOL, ZEC, HYPE). Per-coin weights are not used
  for sizing. Each coin keeps a mode (`spot`, `perp`, `both`); HYPE runs `perp` until it has 600
  contiguous H4 Spot bars, then switches to `both` through the audited active-set switch.
- **Positions:** at most **three open positions across both sleeves**, one per coin. A new signal
  may take a free position; when the book is full it replaces the weakest open position only if its
  ADX exceeds that position's by **more than 5 points**. A position's strength is its ADX while DMI
  still points its way, else zero. Entries are ranked by ADX, then volume ratio.
- **PnL gate:** new entries are allowed only when nothing is open or the open book's price PnL is
  positive.
- **Jev:** the rule creates the candidate, Jev may only confirm it (Spot as today; the Perp short
  needs SELL or STRONG_SELL). A rotation closes the weakest position only **after** the new entry
  is confirmed; a vetoed candidate never closes anything.
- **Risk:** combined daily loss 3% (flat until the next UTC day) and drawdown halt 15%, as ADR-006.
- **Selection code is shared:** `intraday/replay_v2/donchian_rotation_select.py` is used by the
  research replay and by the runtime, so the same inputs give the same decision. The replay's
  reference cases keep their result ids byte for byte after the extraction.

## Operator decision on the fifth coin (2026-10-13)

After the Stage 0 result below (five coins did not improve on four with BTC standing in for HYPE), the operator kept
HYPE as the fifth active coin. Binance Demo lists `HYPEUSDT` on Spot and on USD-M Perp (both `TRADING`, checked against
the public Demo `exchangeInfo` on 2026-10-13, as are the other four coins), so venue availability is not the limit.
The limit is the indicator warm-up: Spot HYPE has 4h bars only from 2026-09-24, so 600 contiguous bars are reached on
**2027-01-02**; Perp HYPE (from 2025-05-30) has had them since 2025-09-07. Until 2027-01-02 HYPE runs `perp`; after that
an audited `active-set switch` to `both` (paused and flat) enables its Spot sleeve.

## Evidence and its limits

- Every number above was measured on data the design was chosen on (27 cases on one window); a
  shorter entry window did not help on three coins (neutral at 25, worse at 20) and its gain on
  four coins comes from ZEC and NEAR while ETH gets worse.
- **HYPE has no measurement.** HYPE has Perp history only from 2025-05-30 and Spot from 2026-09-24, so
  the design was never replayed with it. Stage 0 replayed five coins for three positions with BTC as
  the stand-in (2022 to 2026, entry 25): +96,6% / drawdown 11,4% / 24 rotations against +103,3% /
  9,8% / 8 rotations for the four measured coins; return per drawdown 8,5 against 10,5. In 2026 the
  two baskets are indistinguishable. A fifth coin triples the rotations and costs about two units of
  return per drawdown on the full window, so adding it is a **choice**, not an improvement; BTC is
  less volatile than HYPE, so this is a mechanism check, not a HYPE result.
- **Jev changes the result.** Entries Jev vetoes are skipped, so live fills differ from the replay.
- A per-sleeve position limit and a limit equal to the number of coins were measured and add
  nothing. Larger slots (a position up to a whole sleeve) were measured too: return roughly
  doubles but drawdown reaches 19% to 24% with 20 to 37 daily stops, above the runtime's 15% halt,
  so the slot stays at a third of a sleeve.

## Alternatives considered

- Keep entry 30 (`ZEC-ROT-SLOT` +88,7% / 9,4%): fewer tuned parameters; chosen against by the
  operator because entry 25 measured higher on the seen window. The profile keeps the window a
  single locked constant, so returning to 30 is a profile change, not a redesign.
- Four coins without HYPE: matches the measured basket exactly; chosen against by the operator.
- Per-coin weights as in ADR-006: on three coins fixed thirds 50/50 earned about the same per unit
  of drawdown (7,57 against 7,38 for the slot design). The slot design is chosen for four or more
  coins, where fixed thirds do not apply; its measured edge there is not separated from ZEC's own
  contribution.
- Wait for a prospective verdict: months without a runtime path for the chosen design.

## Consequences

- The Demo result will not equal the research result; the runtime's value is an honest forward
  test with Jev in the loop. A frozen prospective replay of this design (rule hash recorded at
  deployment) keeps a rule-only reference for it.
- The active set grows from three to five coins, so Jev cost grows with the number of active coins
  (about $0,17 per coin per day when called, only for rule candidates).
- Half of the capital sits in the Perp sleeve, which only works in downtrends; it can be idle for
  weeks.
- No source schema migration: the profile is stored through the existing rule parameters and
  active-set extension tables, as in ADR-006.
- Deployment to Binance Demo and any VPS change need separate operator approval and a verified
  backup; this ADR does not authorize them.
