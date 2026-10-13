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

## Addendum 2026-10-13: Spot HYPE on a Perp price proxy, a 400-bar warm-up and a scoped waiver

**Why.** The operator wants Spot HYPE to run on Demo with the other coins now, not after 2027-01-02. Spot HYPE has 4h bars from
2026-09-24 only, and the Setup-2 gate asks for 600 contiguous H4 bars and at least six closed trades per route.

**Decision (operator).**

- **Warm-up 400 for HYPE.** A per-coin `warmup_bars` (400 to 600, default 600) replaces the single constant. Measured on public data
  (ETH, SOL, NEAR, ZEC, 120 H4 bars): with 400 bars or more the trend filters (EMA200, EMA50, ADX, DMI) decide the same as with the
  full history (EMA200 within 0,1%, 0 of 114 passing setups missed); below 300 bars EMA200 differs by up to 3% and decisions can flip.
- **Price-only proxy.** For HYPE, Spot H4 bars before `2026-09-24T08:00Z` (its first own bar) take their OHLC from the HYPE Perp
  series with the volume set to zero. Measured basis over 115 shared bars: Spot above Perp by 0,057% on average, at most 0,125%.
  Stitched series differ from Spot-only history by 0,02% in EMA200 (median) and flip 0,06% to 0,6% of filter decisions, the order of
  the basis itself. **Volume and the volume profile are never proxied** (Spot volume is about 5% of Perp volume): the evaluator
  blocks entries while the volume MA20 window touches proxied bars (`proxy_volume_warmup`) and while the 20-day M15 profile window
  is incomplete (`profile_incomplete`), so the proxy can only warm EMA and ADX, never create an entry.
- **Scoped waiver.** For the Spot route of a coin with a proxy, the replay gate does not require six closed trades; with zero
  closed trades the non-positive-net criterion is waived too. With one to five trades net profit above zero and drawdown below
  15% still apply. The waiver is recorded in the active-set version, shows in every gate report (`waived_*`, limitation
  `setup2_spot_price_proxied_from_perp`) and **lapses by itself** when the Spot market has `warmup_bars` bars of its own.

**What this is not.** The replay of Spot HYPE has almost no trades (volume and profile exist only since 2026-09-24, entries are
possible from 2026-10-14 08:00Z), so the Spot HYPE route passes the gate on process evidence (the 14-day soak, heartbeat,
risk limits and the EMA/ADX history), not on closed-trade evidence like the other routes. Risk is bounded by the slot (a third of
the sleeve), the 5% stop, three positions and the 3% daily loss.

**Check on the stored proxy.** The live evaluator over the HYPE Spot series built from public data (2025-06-01 to 2026-10-13,
2.882 proxied bars plus 116 own bars) produced no Spot entry, with `proxy_volume_warmup` on the first 20 own bars and
`profile_incomplete` on the rest; the latest bar fails the EMA200, EMA50, DMI, volume and profile filters (HYPE is below its
averages). The Perp short rule fired 38 times since its warm-up, the latest on 2026-10-12 20:00Z.

**Removal.** The proxy is data-plumbing for a young market, not a permanent design. When Spot HYPE reaches 600 own bars
(2027-01-02) the operator removes `spot_price_proxy_until` and the waiver through an audited `active-set switch`
(paused and flat); the waiver stops applying on its own at that point.
