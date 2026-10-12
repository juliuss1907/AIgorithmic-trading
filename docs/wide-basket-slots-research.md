# Setup-2 family on a 30-coin basket with five shared slots

Local, deterministic research on already-seen data. It does not touch the runtime, ADR-004/005/006,
the frozen candidate's rule hash or the `replay-v2.2` / `gate-v2.1` pins, and it makes no model calls.

## Question

The ETH-NEAR-SOL study found Setup-2 on H4 steady (2026: +14.2%, DD 10.5%, 65 trades) and every H1
variant weaker or inside the noise. The operator asked to repeat the family on a wider list, with a
cap of five simultaneous positions and a choice of which coin takes a slot when several signal.

## Design (registered before collection and before any run)

**Window.** 2026-01-01 → the latest completed H4 boundary at collection time. Earlier candles only
warm indicators (H4 600 bars, M15 484 hours, as in every other study). Memory limits the window: the
engine holds candles as Decimal objects, so 30 coins over 2022–2026 would not fit in 31 GB.

**Basket.** Top 30 by 90 daily USDT quote volumes among USDT-M perpetuals that are TRADING, have a COIN
underlying, were onboarded before the warmup start and have a TRADING Spot market of the same ticker
listed before the warmup start. Stablecoins and 1000x contracts are excluded. The list is today's, so
it carries **survivorship bias**: coins that rose into the top 30 are over-represented. Any coin whose
history has a gap, a native H4/M15 disagreement or incomplete funding is dropped and the next ranked
coin takes its place; exclusions and reasons are stored in the manifest.

**Slots.** One shared realized-capital pool. At most five positions are open at once, Spot long and
Perp short (isolated, 1x, actual funding) counting together, one per coin. Each slot is 20% of the
pool times the usual ATR multiplier `min(1, 0.02/ATR%)`. There is no 60/40 split. The 3% UTC daily
loss rule and observe-only drawdown are unchanged.

**Selection.** When a bar produces more entries than free slots (a slot released at the same bar counts
as free), entries are ranked by ADX at the trigger bar, then volume divided by its 20-bar mean, then
symbol, and the top ones enter; the rest are logged as `entry_blocked: slot_limit`. The control
`H4-random` picks by a seeded hash instead, to measure what ranking adds.

**Jev.** Not replayed. Jev's state uses order-book depth and spread, open interest, long/short ratio
and sentiment, none of which has history on Binance, so a historical Jev answer would come from a
state the model never sees live. The deterministic ranker stands in for the choice. The `selection`
events record every candidate and score at each decision, so shadow Jev answers collected later can
be joined to the same decisions. Whether Jev picks better is a prospective question, not answered here.

**Cases (14).** H4 reference, H4 with random selection, and the five H1 variants (baseline, GEOM, ADX30,
EMA 50/20, REGIME), each at normal and doubled fee/slippage. The reference for every criterion is
**H4 on this basket with five slots**; the +14.2% of the three-coin basket is context only.

**Criteria.** The five used before (return not below H4, drawdown below 15%, doubled costs positive,
Spot and Perp both positive, both halves positive with the split at the window midpoint). Reported but
not criteria: average open positions, signals blocked by the slot limit, coins traded, share of net
PnL from the five best coins, and trades held beyond 48 hours.

**Predictions (written before the run).** H4 stays at or above every H1 variant; ranking by ADX beats
random selection by an amount inside the noise; no H1 variant separates from H4 meaningfully.

## Limits

- Seen-data research on a window of about nine months; the number of trades is small and
  differences between cases will mostly be noise. Read it descriptively.
- Survivorship bias in the basket; no point-in-time ranking is available from Binance.
- Jev is not part of the result (see above).
- A pass proposes at most a new frozen prospective test; it activates nothing.

## Run

```bash
uv run python -m intraday.replay_v2.donchian_wide_data \
  --output-root /home/julius/.local/state/aigorithmic-trading/reports/donchian-wide-data-20261011
uv run python -m intraday.replay_v2.donchian_adx_study --universe wide-slots \
  --inputs /home/julius/.local/state/aigorithmic-trading/reports/donchian-wide-data-20261011/manifest.json \
  --report-root /home/julius/.local/state/aigorithmic-trading/reports/donchian-wide-slots-20261011
uv run python -m intraday.replay_v2.donchian_h1_verdict \
  /home/julius/.local/state/aigorithmic-trading/reports/donchian-wide-slots-20261011/runs/comparison.json
```

## Result (2026-10-11)

Data: 30 coins, 2026-01-01 → 2026-10-11 08:00 UTC (collected today; two ranked candidates, QNT and LSK,
were dropped for native Spot H4/M15 disagreements and replaced). Engine commit `bad14c1`, report root
`donchian-wide-slots-20261011`; 14 cases, each with a deterministic double replay (27 minutes, 8.8 GB).
The three-coin family re-run on the new engine reproduced all 12 earlier 2026 results byte for byte.

| Case | Return % | DD % | Trades | Fees + slippage | Spot net | Perp net | Daily stops | Return at cost ×2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **H4, ranked** (reference) | **+23.40** | **30.59** | 328 | 146 | +287 | −53 | 7 | +9.85 |
| H4, random (seed `wide-slots-v1`) | +3.57 | 32.00 | 339 | 148 | +112 | −76 | 10 | −10.82 |
| H1 baseline | −5.89 | 49.20 | 1,256 | 748 | +242 | −301 | 30 | −48.46 |
| H1-GEOM | −32.03 | 52.39 | 648 | 284 | −195 | −125 | 47 | −46.13 |
| H1-ADX30 | +3.87 | 36.04 | 868 | 486 | +221 | −182 | 13 | −36.23 |
| H1-EMA50-20 | −4.16 | 50.23 | 1,260 | 763 | +229 | −270 | 28 | −46.67 |
| H1-REGIME | −6.63 | 41.78 | 832 | 405 | +213 | −279 | 12 | −41.31 |

**No H1 variant advances;** all five fail every criterion except none (return below the H4 reference,
drawdown far above 15%, doubled costs negative, Perp negative, the second half negative).

What the run shows:

- **Wider and slotted lifts return and drawdown together.** H4 on 30 coins makes +23.4% against +14.2%
  on ETH-NEAR-SOL, but drawdown triples (30.6% vs 10.5%) and the 3% daily stop fires 7 times. Five
  20% slots of mostly correlated alts can be almost fully invested with no 60/40 cap. Return per unit
  of drawdown is 0.76 against 1.36. It also fails the 15% drawdown criterion that guards the live halt.
- **Ranking matters in this window.** A supplementary check (not pre-registered) drew ten random
  selections of the same H4 case: mean +6.2%, standard deviation 7.1, range −3.7% to +18.3%, drawdown
  29.9–32.7%. The ADX ranking's +23.4% is above all ten draws (about 2.4 standard deviations over the
  mean), while drawdown is unchanged: selection changed which coins were bought, not how deep the
  book fell. The ranker was fixed before the run; the ten seeds were added afterwards because one draw
  is a weak control.
- **Concentration and path.** Fifteen of 30 coins finished positive; the five best made +428 on a
  net +234. The account gained +236 USDT by 2026-05-22 and was flat afterwards (−2); equity peaked near
  1,290 in June and fell about 18% in July.
- **H1 still loses to costs.** 1,256 trades cost 748 USDT, and Perp shorts lose in every H1 case.
- **Slots bind often.** H4 left 843 signals unfilled, H1 baseline 3,368; average open positions are
  2.9 (H4) and 2.8 (H1), so the limit is hit in bursts, not constantly.
- **Jev is untouched.** Everything above is a deterministic ranker standing in for the choice.

Limits repeat: about nine months, seen-data selection of candidates, a current-day top-30 list with
survivorship bias (PAXG, a gold token, is included because Binance tags it as COIN), Jev not replayed.

**Conclusion.** H4 remains the only family worth carrying forward; H1 is closed. The wide basket is
not adoptable as run (drawdown 30.6%), but the ranking effect is the most interesting finding and is
the natural thing to test next, together with a smaller slot size or a exposure cap that brings the
drawdown inside the live limit. Both would be new, pre-registered experiments.

## Addendum: re-collection with longer M15 history (2026-10-12)

The slow H1 regime candidate (see `setup2-h1-research.md`, third round) needs 1,000 hours of M15
before the window start. The collector gained `--m15-warmup-hours` (default 484, recorded in the
manifest as `m15_warmup_hours`); the 30 coins are collected again into a new directory with 1,000.
The H4 and `H1-REGIME` cases are re-run from it and must reproduce the earlier report exactly, since
every other series is cut back to its fixed warmup before use. Only `H1-SLOWREGIME` reads the extra
history. Case subsets run with `donchian_adx_study --only`.
