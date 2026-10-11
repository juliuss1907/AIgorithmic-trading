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
