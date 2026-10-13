# Rotating positions, two sleeves, PnL gate and 5% stop (research)

Offline research on seen data. It does not touch the runtime, the VPS, ADR-004/005/006 or the frozen
ETH-NEAR-SOL rule hash `bf6fccc9…`. Branch `research/rotating-slots`, registered before any case ran.

## Why

Julius asked for a design that differs from the frozen basket: four coins (HYPE added), no fixed per-coin
weights, at most three open positions, a new signal that must close an open position to enter, separate
Spot and Perp sleeves at 50/50, reinvestment of realized profit, entries only when PnL is positive and a 5%
stop per position. The frozen basket and the H1 and wide-slot studies do not cover this, so it is measured
here before any runtime change.

## Design (one case family, every parameter closed under Literals)

| Item | Rule |
|---|---|
| Signals | Setup-2 H4 unchanged: Donchian 30/10, EMA200/50, ADX14 > 20 and rising, volume ×1,2, profile, ATR14×3 |
| Sleeves | Spot 50% and Perp 50% of 1.000 USDT, separate realized capital each, no transfers |
| Per-coin weights | None. A position uses the free principal of its own sleeve times `min(1, .02/ATR%)` |
| Position limit | 3 open positions across both sleeves, at most one per coin |
| Full book | A new signal may enter only by closing the weakest open position at that bar's open: weakest = lowest ADX counted only while DMI still points the position's way; the candidate's ADX must exceed it by 5 points, else the signal is skipped. Entries are ranked by ADX then volume ratio |
| PnL gate | New entries only when nothing is open or the open book's price PnL (marked at that bar's open) is above zero |
| Stop | Tighter of the 5% stop from entry and the ATR×3 trail; the trail ratchets, never loosens |
| Reinvestment | Realized-only, per sleeve, net of costs and funding (the research engine's existing rule) |
| Costs and risk | Spot 10/5 bps, Perp 5/5 bps, actual funding, combined UTC daily loss 3%, drawdown observe-only |

**How "PnL dương" was read.** The request left open whose PnL. The registered reading is the open book:
add positions only while the positions already open are in profit. Two other readings are possible and are
not tested: realized account PnL above zero (it would stop the system for good after the first deficit,
because no trades means no recovery) and "only close a profitable position to make room".

**What three coins cannot test.** With one position per coin, a three-position limit never binds on three
coins, so rotation is exercised only with a fourth coin. HYPE has no history in the frozen data (Perp from
2025-05-30, Spot from 2026-09-24), so **ZEC stands in as the fourth coin**: a volatile altcoin with both
markets and four years of data. This tests the mechanism, not HYPE.

## Cases (18, normal and doubled costs where marked)

ETH-NEAR-SOL, 2022-01-01 → 2026-10-02:

| Case | Meaning |
|---|---|
| `H4` (+cost2x) | Frozen 60/40 fixed thirds, the ADR-005 case; must reproduce +81,75% / 11,58% / 423 trades |
| `FIXED5050` (+cost2x) | Same fixed thirds with the split moved to 50/50 (the 50/50 backtest Julius approved) |
| `ROT` (+cost2x) | The design above |
| `ROT-SLOT` (+cost2x) | The design with a position capped at a third of its sleeve (the earlier proposal) |
| `ROT-NOGATE`, `ROT-NOSTOP`, `ROT-BARE` | One mechanism removed, or both |

ETH-NEAR-SOL-ZEC (four coins, the limit binds):

| Case | Meaning |
|---|---|
| `ROT` (+cost2x), `ROT-SLOT` (+cost2x) | The design with rotation, and the slot-capped variant |
| `ROT-NOLIMIT` | Same rules, four positions allowed (the limit removed): the control for the limit |
| `ROT-NOROT` | Limit kept, a full book simply skips new signals |
| `ROT-BARE` | No gate and no 5% stop |

## Mechanical criteria (fixed before the run)

For `ROT` and `ROT-SLOT` in each family, the control is `FIXED5050` (three coins) or `ROT-NOLIMIT` (four):

1. Net return not below the control.
2. Max drawdown below 15%.
3. Return at doubled costs positive.
4. Spot net and Perp net both positive.
5. Both halves positive (split 2024-10-29).

Ablations are descriptive. A pass proposes a frozen prospective test; it never activates anything.

## Predictions (written before the run)

- `H4` reproduces the ADR-005 numbers byte for byte.
- `FIXED5050` lands slightly below `H4` (Spot earned more than Perp).
- `ROT` **does not beat `FIXED5050` on return**: the 5% stop sits inside 3 ATR for NEAR and SOL on H4 and the
  H1 study showed that a tighter stop geometry cuts the long tail that Setup-2 lives on; the gate also skips
  entries that H4 would have taken.
- `ROT` drawdown is higher than `ROT-SLOT` because one position can use a whole sleeve.
- `ROT-NOLIMIT` ≥ `ROT` on return; rotation is a risk and focus choice, not a return source.

## Limits

Seen data, one window, a stand-in for HYPE, survivor coins chosen after the fact (the frozen basket was the
second of ten on seen data). No Jev, so a runtime version differs by the confirmations Jev removes.

## Results (2022-01-01 → 2026-10-02, seen data, 18 cases, all reproduced on a second run)

Private root: `~/.local/state/aigorithmic-trading/reports/donchian-rotation-20261012/`. The study stopped once at
case 9 when the disk filled and was resumed with `--resume` from the same binding; cases 1–8 were re-verified.

ETH-NEAR-SOL (three coins, so the position limit cannot bind):

| Case | Return | Max DD | Return/DD | Trades | Daily stops | Cost ×2 |
|---|---:|---:|---:|---:|---:|---:|
| `H4` 60/40 fixed thirds (ADR-005) | +81,75% | 11,58% | 7,06 | 423 | 0 | +57,4% |
| `FIXED5050` fixed thirds | +78,72% | 10,40% | 7,57 | 424 | 1 | +55,7% |
| `ROT` uncapped, gate, 5% stop | **+152,66%** | **19,17%** | 7,96 | 381 | **20** | +86,7% (DD 21,8%) |
| `ROT-SLOT` | +73,24% | 9,93% | 7,38 | 438 | 1 | +50,6% |
| `ROT-NOGATE` | +146,63% | 20,53% | 7,14 | 388 | 20 | |
| `ROT-NOSTOP` | +131,25% | 24,20% | 5,42 | 365 | 22 | |
| `ROT-BARE` | +138,59% | 24,20% | 5,73 | 385 | 20 | |

ETH-NEAR-SOL-ZEC (four coins, limit of three binds; ZEC stands in for HYPE):

| Case | Return | Max DD | Return/DD | Trades | Daily stops | Rotations | Cost ×2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ROT` | +153,50% | 21,57% | 7,12 | 483 | 28 | 6 | +115,3% |
| `ROT-SLOT` | +88,72% | 9,40% | 9,44 | 548 | 2 | 8 | +59,1% |
| `ROT-NOLIMIT` (four positions) | +184,75% | 22,50% | 8,21 | 491 | 28 | | |
| `ROT-NOROT` (full book skips) | +157,02% | 21,57% | 7,28 | 480 | 29 | | |
| `ROT-BARE` | +197,46% | 21,81% | 9,05 | 474 | 37 | 6 | |

**Mechanical verdict: no case advances.** `ROT` fails criterion 2 (DD 19,17% and 21,57%, limit 15%). `ROT-SLOT`
passes 2–5 and fails criterion 1: it earns less than its control (`FIXED5050`; in the four-coin family the
registered control `ROT-NOLIMIT` is uncapped, so that comparison is not like for like).

### Predictions scored

| Prediction | Outcome |
|---|---|
| `H4` reproduces ADR-005 byte for byte | Yes (+81,745%, 11,58%, 423 trades) |
| `FIXED5050` slightly below `H4` | Yes (+78,72% against +81,75%) |
| `ROT` does not beat `FIXED5050` on return | **No**: +152,66% against +78,72% |
| `ROT` drawdown above `ROT-SLOT` | Yes (19,17% against 9,93%) |
| `ROT-NOLIMIT` ≥ `ROT` on return | Yes (+184,75% against +153,50%) |

### What the numbers say

- **The extra return of the uncapped design is size, not edge.** Return per drawdown is about the same
  (7,1–8,0 across designs, 7,06 for the frozen basket). Letting one position use the whole sleeve roughly
  doubles return and drawdown together.
- **Uncapped size fights the daily-loss rule.** One full-size stop-out costs about 3% of the account, the
  daily limit, so `ROT` hits 20 to 37 daily stops over 4,75 years against 0 to 2 for the capped designs.
- **Rotation almost never acts.** Six rotations in 4,75 years (eight for the slot variant); the candidate fails
  the 5-point margin 54 to 66 times. Skipping the signal (`NOROT`) earns slightly more than rotating.
- **The three-position limit costs return**: +184,75% without it against +153,50% with it (+157,02% when
  full books just skip), at the same drawdown. It is a concentration choice, not a return source.
- **The 5% stop helped on three coins** (+152,7% against +131,3% without it, DD 19,2% against 24,2%), against the
  H1-style expectation that it would clip the long tail. Holds over 48 h fell only from 197 to 186.
- **The PnL gate is marginal**: +152,7% against +146,6% on three coins; with the fourth coin the bare design
  earned more (+197,5% against +153,5%) at the same drawdown, so gate plus stop did not buy lower drawdown there.
- **The four-coin result carries ZEC, not HYPE.** HYPE has no history; its Perp from 2025-05-30 and Spot from
  2026-09-24 would need their own collection.

### Consequence for the Demo design

The design as asked (uncapped, three positions, no per-coin weight) would breach the runtime's 15% drawdown
halt and its 3% daily stop repeatedly. The slot-capped variant keeps drawdown near 10% and costs roughly half
the return. No frozen candidate changes; ADR-005 stays the only prospective test.

## Re-run on 2026 only (registered before the run)

Same 18 pre-registered cases and the same five criteria, a fresh 1.000 USDT account from 2026-01-01 to
2026-10-02 (`--start 2026-01-01`; earlier rows only warm the indicators). The criteria's half split is the
window midpoint. Julius asked for it to see whether the full-window picture holds in the recent regime.

Predictions, written before the run:

- `H4` reproduces the earlier 2026 result (+14,23%, DD 10,46%, 65 trades, cost ×2 +11,38%).
- The uncapped `ROT` earns again roughly twice `FIXED5050` with roughly twice its drawdown; return per drawdown
  stays close to the controls'. Its drawdown can stay under 15% in a shorter window, so criterion 2 is the one
  most likely to flip; it is not evidence that the design is safer.
- The slot variants stay near the controls' drawdown and return.
- Rotation acts only a handful of times (the window holds about a fifth of the signals).
- Trade counts are small (about 60 to 110), so differences of a few points are noise.

### 2026-only results (2026-01-01 → 2026-10-02, fresh account, 18 cases, all reproduced)

Private root: `~/.local/state/aigorithmic-trading/reports/donchian-rotation-2026ytd-20261012/`. `H4` reproduces the earlier
2026 run exactly (+14,23%, 10,46%, 65 trades, cost ×2 +11,38%).

| Case | Return | Max DD | Return/DD | Trades | Daily stops | Rotations | Cost ×2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `H4` 60/40 fixed thirds | +14,23% | 10,46% | 1,36 | 65 | 0 | | +11,4% |
| `FIXED5050` | +14,20% | 10,34% | 1,37 | 65 | 0 | | +11,5% |
| `ROT` (three coins) | +17,97% | 11,38% | 1,58 | 56 | 1 | | +13,7% |
| `ROT-SLOT` (three coins) | +15,51% | 9,49% | 1,63 | 64 | 0 | | +12,8% |
| `ROT-NOGATE` / `NOSTOP` / `BARE` | +20,84% / +25,25% / +28,47% | 11,38% | 1,83 / 2,22 / 2,50 | 56 / 53 / 53 | 1 / 2 / 2 | | |
| `ZEC-ROT` (four coins) | +34,00% | 14,55% | 2,34 | 75 | 2 | 2 | +27,9% (DD 15,0%) |
| `ZEC-ROT-SLOT` | +22,81% | 9,43% | 2,42 | 82 | 0 | 1 | +19,4% |
| `ZEC-ROT-NOLIMIT` / `NOROT` / `BARE` | +33,23% / +33,11% / +40,07% | 16,36% / 16,11% / 16,42% | 2,03 / 2,05 / 2,44 | 76 / 74 / 72 | 3 / 3 / 4 | | |

**Mechanical verdict: `ROT`, `ROT-SLOT` (three coins) and `ZEC-ROT` advance.** `ZEC-ROT-SLOT` fails criterion 1 only
against the uncapped `NOLIMIT` control.

Predictions scored: `H4` reproduces (yes); `ROT` again about twice `FIXED5050` with twice its drawdown (**no**:
+1,27× return and +1 point of drawdown); criterion 2 is the likeliest to flip (yes, `ROT` stays under 15%); slot variants
stay near the controls (yes); rotation acts a handful of times (yes, two or fewer); trade counts are small (yes, 53 to 82).

How to read it:

- **It is a sub-period of the window already seen, not new evidence.** The full window still fails criterion 2
  for the uncapped design (DD 19,2% and 21,6%); 2026 alone does not.
- **The uncapped design's advantage shrinks to about 4 points** over `FIXED5050` with only 56 trades; that is inside
  the noise of a nine-month window.
- **The stop is not a stable result.** It helped on the full window (+152,7% against +131,3%) and cost 7 to 10 points in 2026
  (+17,97% against +25,25%).
- **The four-coin edge is ZEC's 2026, not HYPE and not the mechanism.** Spot carried it (+301 USDT against +39 USDT for Perp),
  and the limit of three positions and rotation add nothing: `NOLIMIT` +33,2%, `NOROT` +33,1%, `ROT` +34,0%.
- **Most of the profit is in the second half** of the window in every case (first half +23 USDT, second +157 USDT for `ROT`).

## Donchian entry 25 and 20, and three positions per sleeve (registered before the run)

Julius asked to try the entry window at 25 and 20 (exit stays 10, as in the A4 `20/10` and `30/10` pairs) and a limit of three
positions in each sleeve (six at most) in place of three in total. Base design: the slot-capped `ROT-SLOT` (a position at
most a third of its sleeve, 5% stop, open-book PnL gate, rotation with a 5-point ADX margin). Universe `rotation-window`.

**The per-sleeve limit** counts positions per sleeve and drops the one-position-per-coin rule across sleeves, so a coin may
hold Spot long and Perp short together. Three coins can then reach six positions only if every coin is long in Spot and short in
Perp at once, which opposite trends almost rule out. With four coins the per-sleeve limit mainly lifts the ceiling from three to four.
Exposure is up to 100% of capital instead of about 50%.

Thirty cases per window (2022-01-01 → 2026-10-02 and 2026 only), all H4:

| Group | Cases |
|---|---|
| Controls at window 30 | `H4` 60/40 and `FIXED5050`, normal and cost ×2 |
| Fixed-thirds window controls | `FIXED5050` at 25 and 20, normal cost |
| ETH-NEAR-SOL and ETH-NEAR-SOL-ZEC | `ROT-SLOT` (three in total) and `ROT-SLOT6` (three per sleeve) × windows 30, 25, 20, each normal and cost ×2 |

Controls for the five criteria: a design at 25 or 20 against the same design at 30; `ROT-SLOT6` at 30 against `ROT-SLOT` at 30;
`ROT-SLOT` at 30 against `FIXED5050` on three coins. Descriptive extras: trades, costs, rotations, gate and limit blocks, and
the most positions open at once per sleeve.

Predictions, written before the run:

- The 30 cases reproduce the earlier numbers exactly: `H4` +81,75% / 11,58% / 423; `ROT-SLOT` three coins +73,24% / 9,93% / 438
  (2026: +15,51% / 9,49% / 64) and with ZEC +88,72% / 9,40% / 548 (2026: +22,81% / 9,43% / 82).
- Shorter entry windows add trades (15 to 40%) and costs and do **not** beat 30 on the full window, as A4 `20/10` did not on
  2022-2024 (+33,07% against +34,87%, DD 11,75% against 10,47%). In 2026 the differences stay inside the noise.
- Three coins: `ROT-SLOT6` equals `ROT-SLOT` apart from the freed one-per-coin rule, because the limit never binds. With ZEC
  it earns more and draws down more, since exposure can double.
- Six simultaneous positions practically never occur; the observed peak is three Spot or three Perp.
- Rotation stays rare.

### Results: entry window 30, 25, 20 and the per-sleeve limit (60 cases, all reproduced)

Private roots: `…/donchian-rotation-window-20261012/` (2022-01-01 → 2026-10-02) and
`…/donchian-rotation-window-2026ytd-20261012/` (2026 only). Window-30 reference cases reproduce the earlier results exactly in both.
`ROT-SLOT6` (three per sleeve) equals `ROT-SLOT` (three in total) in every case, so each row stands for both.

Full window, return / max DD / trades (cost ×2 return):

| Basket | Window 30 | Window 25 | Window 20 |
|---|---|---|---|
| ETH-NEAR-SOL fixed thirds 50/50 | +78,72% / 10,40% / 424 | +77,68% / 10,42% / 435 | +72,57% / 11,59% / 446 |
| ETH-NEAR-SOL `ROT-SLOT` | +73,24% / 9,93% / 438 (+50,6%) | +73,12% / 10,60% / 449 (+49,9%) | +68,55% / 11,58% / 460 (+45,9%) |
| ETH-NEAR-SOL-ZEC `ROT-SLOT` | +88,72% / 9,40% / 548 (+59,1%) | +103,30% / 9,83% / 553 (+71,0%) | +94,15% / 10,69% / 570 (+62,4%) |

2026 only:

| Basket | Window 30 | Window 25 | Window 20 |
|---|---|---|---|
| ETH-NEAR-SOL fixed thirds 50/50 | +14,20% / 10,34% / 65 | +14,13% / 10,41% / 66 | +15,48% / 10,52% / 67 |
| ETH-NEAR-SOL `ROT-SLOT` | +15,51% / 9,49% / 64 | +15,44% / 9,56% / 65 | +16,80% / 9,68% / 66 |
| ETH-NEAR-SOL-ZEC `ROT-SLOT` | +22,81% / 9,43% / 82 | +22,34% / 9,51% / 83 | +22,90% / 9,65% / 85 |

Most positions open at once: **three** in every case (Spot or Perp), never four and never six, even with the per-sleeve limit.

**Mechanical verdict.** Full window: `ROT-SLOT6` three coins and the four-coin `ZEC` cases at 25 and 20 advance; the three-coin windows 25
and 20 fail criterion 1. 2026: seven cases advance, all by margins of 0,1 to 1,3 points on 53 to 85 trades, i.e. ties and noise.
`ROT-SLOT6` "passes" only because it is identical to its control.

### Predictions scored

| Prediction | Outcome |
|---|---|
| Window-30 cases reproduce | Yes, all of them |
| Shorter windows add 15 to 40% more trades | **No**: +2% to +5% (full window), +1% to +4% (2026) |
| Shorter windows do not beat 30 on the full window | Yes on three coins (−0,1 and −4,7 points); **no** on four (+14,6 and +5,4) |
| 2026 differences stay in the noise | Yes (−0,5 to +1,3 points) |
| `ROT-SLOT6` equals `ROT-SLOT` on three coins | Yes |
| With ZEC, `ROT-SLOT6` earns and draws down more | **No**: identical |
| Six simultaneous positions practically never occur | Yes, the peak is three |
| Rotation stays rare | Yes (8 to 9 over the full window, 1 in 2026) |

### What the numbers say

- **A limit per sleeve changes nothing.** Every blocked signal (66 `rotation_margin` blocks in the four-coin full window) happened in
  the same sleeve as the positions already open: coins trend together, so three Spot longs are followed by a fourth Spot signal, and
  Spot long against Perp short are opposite trends. The ceiling of six is theoretical; three is the working ceiling.
- **A shorter entry window is not an improvement.** On three coins it is neutral at 25 and worse at 20 (−4,7 points, DD +1,7 points,
  cost ×2 return −4,7 points), the same direction as A4 `20/10` on 2022-2024. On four coins it is better, but through the coin mix:
  ZEC +99 USDT and NEAR +58 at window 25 while ETH loses 27 (Perp ETH +61 → +27 → +2 as the window shortens).
- **ETH is the coin that gets worse with shorter windows** in both baskets, NEAR and ZEC improve, SOL is flat. A window chosen
  on this pattern would be tuned to which coins are in the basket.
- **Trades barely rise** (+2% to +5%), so costs are not what separates the windows; the signals themselves are.
- **2026 alone cannot tell the windows apart** (all within ±1,3 points).

Conclusion: keep entry 30. The per-sleeve limit adds nothing over the pooled limit; if a limit is kept at all, the simpler
three in total does the same.

## Per-sleeve limit raised to the number of coins (registered before the run)

The previous run showed that three positions per sleeve never bind on three coins and, on four coins, only blocked a fourth
signal that arrived in the same sleeve as three open positions (66 `rotation_margin` blocks, never a 4th position in the other
sleeve). Julius asked what happens when the per-sleeve limit equals the number of coins in the basket.

- **Three coins:** three per sleeve is already the number of coins, so the case is the `ROT-SLOT6` already run (identical to
  `ROT-SLOT`). Nothing new there.
- **Four coins (ETH-NEAR-SOL-ZEC, ZEC for HYPE):** four per sleeve, up to eight positions in all. This is the only new case.

**A confound in the engine.** A slot is sized at 1/`max_positions` of its sleeve, so raising the limit from three to four also
shrinks every position from 1/3 to 1/4 of the sleeve. To separate the two effects the study adds a `slot_divisor` (default
`max_positions`, so earlier cases are unchanged) and runs, on four coins, entry 30 / exit 10, slot design, per-sleeve limit:

| Case | Per sleeve | Slot | Reads as |
|---|---:|---:|---|
| `ROT-SLOT6` (control, already run) | 3 | 1/3 | reference |
| `ROT-SLOT4S` | 4 | 1/4 | Julius's request: limit and size together |
| `ROT-SLOT4S-THIRD` | 4 | 1/3 | the limit alone |
| `ROT-SLOT6-QUARTER` | 3 | 1/4 | the size alone |

Each at normal cost and cost ×2, on the full window (2022-01-01 → 2026-10-02) and on 2026 alone. Universe `rotation-capacity`.
Criteria: the five earlier ones, control `ROT-SLOT6`.

Predictions, written before the run:

- `ROT-SLOT6` reproduces +88,72% / DD 9,40% / 548 trades (2026: +22,81% / 9,43% / 82) and cost ×2 +59,05% (2026: +19,40%).
- A fourth position per sleeve can now open, so the peak concurrency reaches four in `4S` and `THIRD`, three in `QUARTER`.
- `ROT-SLOT6-QUARTER` (smaller positions, same count) earns less than the control with a lower drawdown: roughly the control
  scaled by 3/4 where the book is full, so about +65% to +80% and DD about 7% to 9%.
- `ROT-SLOT4S-THIRD` (more positions at the same size) earns more with a higher drawdown, since exposure per sleeve can
  exceed 100% only up to the free principal: about +95% to +115% and DD about 10% to 13%.
- `ROT-SLOT4S` lands between the two: return within ±15% of the control and DD within ±1,5 points. It is a diversification
  of the same per-sleeve exposure (4 × 1/4 against 3 × 1/3), not extra leverage, so return per drawdown stays near the control's.
- In 2026 the differences stay inside the noise (a few points on about 80 trades).

### Results: per-sleeve limit equal to the number of coins (four coins, 16 cases, all reproduced)

Private roots: `…/donchian-rotation-capacity-20261013/` (2022-01-01 → 2026-10-02) and
`…/donchian-rotation-capacity-2026ytd-20261013/` (2026 only). `ROT-SLOT6` reproduces +88,72% / 9,40% / 548 (2026: +22,81% / 9,43% / 82).

Full window, return / max DD / return per DD / trades (cost ×2 return):

| Case | Per sleeve | Slot | Return / DD | Ret/DD | Trades | Cost ×2 | Peak open |
|---|---:|---:|---|---:|---:|---:|---:|
| `ROT-SLOT6` (control) | 3 | 1/3 | +88,72% / 9,40% | 9,44 | 548 | +59,1% | 3 |
| `ROT-SLOT4S` (Julius's request) | 4 | 1/4 | +68,81% / 8,24% | 8,35 | 568 | +48,0% | 4 |
| `ROT-SLOT4S-THIRD` (limit alone) | 4 | 1/3 | +90,17% / 10,46% | 8,62 | 572 | +59,8% | 4 |
| `ROT-SLOT6-QUARTER` (size alone) | 3 | 1/4 | +68,92% / 7,26% | 9,50 | 542 | +48,8% | 3 |

2026 only:

| Case | Return / DD | Ret/DD | Trades | Cost ×2 |
|---|---|---:|---:|---:|
| `ROT-SLOT6` | +22,81% / 9,43% | 2,42 | 82 | +19,4% |
| `ROT-SLOT4S` | +17,13% / 8,18% | 2,09 | 87 | +14,5% |
| `ROT-SLOT4S-THIRD` | +22,92% / 10,49% | 2,18 | 86 | +19,4% |
| `ROT-SLOT6-QUARTER` | +17,06% / 7,20% | 2,37 | 82 | +14,6% |

**Mechanical verdict.** `THIRD` advances in both windows (return ≥ control, DD < 15%, cost ×2 positive, both halves positive).
`4S` and `QUARTER` fail criterion 1 in both windows: they earn less than the control, by construction, because every position is a
quarter of a sleeve instead of a third. The `THIRD` pass is +1,5 points on the full window and +0,1 in 2026, which is no evidence.

Predictions scored:

| Prediction | Outcome |
|---|---|
| `ROT-SLOT6` reproduces | Yes, both windows |
| Peak concurrency four in `4S`/`THIRD`, three in `QUARTER` | Yes; never more than four per sleeve, eight at once never happened |
| `QUARTER` +65% to +80%, DD 7% to 9% | Yes (+68,92%, 7,26%) |
| `THIRD` +95% to +115%, DD 10% to 13% | Return **no** (+90,17%); DD yes (10,46%) |
| `4S` return within ±15% and DD within ±1,5 points of the control | Return **no** (−22%); DD yes (−1,2 points) |
| 2026 differences a few points | `THIRD` yes (+0,1); the quarter-size cases −5,7 points, not noise on 82 trades |

What the numbers say:

- **Raising the limit to the number of coins adds almost nothing.** With the slot kept at 1/3 the extra position gives +1,5 points of
  return and +1,1 points of drawdown (2026: +0,1 and +1,1). Return per drawdown falls (9,44 → 8,62).
- **The slot size is the knob.** The quarter-size cases earn 20 points less and draw down 1 to 2 points less, with the same return per
  drawdown as the control (`QUARTER` 9,50, control 9,44). `4S` is `QUARTER` plus the fourth slot: +68,81% against +68,92%.
- **With limit = coins and slot = 1/coins, the design collapses into fixed per-coin weights.** A coin always has its own slot, so there is
  no rotation (zero in `4S` and `THIRD`) and no position-limit block; what is left is the PnL gate and the 5% stop on 50/50 fixed
  weights, which is the `FIXED5050` family of the first study.
- **Daily stops fall from 2 to 0 with the quarter slot** on the full window; in 2026 there were none anywhere.
- The 4S result is below what the earlier "limit never binds, so nothing changes" reading would give, because that reading missed that the slot
  shrinks with the limit. The confound is why the study split size from limit.

Conclusion: keep three positions per sleeve (or three in total) at 1/3 of a sleeve. If a lower drawdown is wanted, shrinking the slot
buys it at a proportional cost in return, not a better trade-off.

## Five coins for three positions (ADR-007 stage 0, registered before the run)

The runtime of [ADR-007](decisions/007-slot-rotation-five-coin-runtime.md) runs five coins (ETH, NEAR, SOL, ZEC, HYPE). Every
measurement so far used at most four. HYPE has no Spot history (Spot from 2026-09-24), so it cannot be replayed; **BTC stands
in as the fifth coin**. This tests five coins competing for three positions, not HYPE (BTC is far less volatile).

Universe `rotation-five`: slot design (a third of a 50% sleeve, three positions, PnL gate, 5% stop, rotation), entry window
30 and 25, normal cost and cost ×2, on ETH-NEAR-SOL-ZEC (the measured control) and on BTC-ETH-NEAR-SOL-ZEC. Eight cases, windows
2022-01-01 → 2026-10-02 and 2026 only. Control for the five criteria: the four-coin case with the same entry window.

Predictions, written before the run:

- The four-coin cases reproduce: entry 30 +88,72% / 9,40% / 548 (2026 +22,81% / 9,43% / 82) and entry 25 +103,30% / 9,83% / 553
  (2026 +22,34% / 9,51% / 83).
- A fifth coin adds signals: trades rise 10% to 25% and rotations at least double (four coins: 8 and 8 in the full window).
- Return within ±20% of the four-coin case and drawdown below 12% (the runtime halts at 15%); return per drawdown within ±1,5.
  No improvement is expected: BTC adds signals but few large moves, and a third position is still the cap.
- The peak of simultaneous positions stays three.
- In 2026 the difference stays within a few points on about 80 to 100 trades (noise).

### Results: five coins for three positions (BTC stands in for HYPE; 8 cases, both windows)

Private roots: `…/donchian-rotation-five-20261013/` and `…/donchian-rotation-five-2026ytd-20261013/`. The four-coin controls reproduce
exactly (+88,72% / 9,40% / 548 and +103,30% / 9,83% / 553).

| Window | Basket | Entry | Return / DD | Return/DD | Trades | Rotations | Cost ×2 |
|---|---|---:|---|---:|---:|---:|---:|
| 2022 → 2026 | ETH-NEAR-SOL-ZEC | 30 | +88,72% / 9,40% | 9,44 | 548 | 8 | +59,1% |
| 2022 → 2026 | + BTC | 30 | +84,38% / 11,93% | 7,07 | 641 | 24 | +49,5% |
| 2022 → 2026 | ETH-NEAR-SOL-ZEC | 25 | +103,30% / 9,83% | 10,51 | 553 | 8 | +71,0% |
| 2022 → 2026 | + BTC | 25 | +96,58% / 11,35% | 8,51 | 647 | 24 | +59,0% |
| 2026 | ETH-NEAR-SOL-ZEC | 30 | +22,81% / 9,43% | 2,42 | 82 | 1 | +19,4% |
| 2026 | + BTC | 30 | +23,68% / 9,85% | 2,40 | 94 | 4 | +19,5% |
| 2026 | ETH-NEAR-SOL-ZEC | 25 | +22,34% / 9,51% | 2,35 | 83 | 1 | +18,9% |
| 2026 | + BTC | 25 | +21,99% / 9,85% | 2,23 | 97 | 4 | +17,7% |

Mechanical verdict: the five-coin cases pass four of five criteria on the full window and fail criterion 1 (return below the
four-coin control); in 2026 entry 30 passes and entry 25 fails it by 0,35 points. Peak simultaneous positions stay three.

Predictions scored:

| Prediction | Outcome |
|---|---|
| Four-coin cases reproduce | Yes |
| Trades rise 10% to 25% | Yes (+17% on both entries; 2026 +15% and +17%) |
| Rotations at least double | Yes (8 → 24 on the full window, 1 → 4 in 2026) |
| Return within ±20%, drawdown below 12% | Yes (−5% and −7%; DD 11,93% and 11,35%, entry 30 only 0,07 below the line) |
| Return per drawdown within ±1,5 | **No** on the full window (−2,4 and −2,0); yes in 2026 |
| Peak of three simultaneous positions | Yes |
| 2026 within a few points | Yes (+0,9 and −0,4 points) |

What it says:

- **A fifth coin adds churn, not return.** Rotations triple (24 against 8), return falls about 5% to 7% and drawdown rises 1,5 to 2,5
  points on the full window, so return per drawdown drops by about 2. In 2026 the two baskets are indistinguishable.
- **BTC is not HYPE.** BTC is low volatility with fewer large moves; a volatile coin such as HYPE may behave closer to ZEC. The
  measurement tests the mechanism (five signals competing for three slots), not the coin.
- **Entry 25 keeps its edge over entry 30 on the full window with five coins** (+12,2 points) and loses it in 2026 (−1,7 points), the
  same pattern as with four coins.
