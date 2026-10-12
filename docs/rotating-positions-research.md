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
