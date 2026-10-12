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
