# Setup-2 at H1: pre-registered research candidate

Local, deterministic research on already-seen data. It does not touch the runtime, the frozen
[ADR-005](decisions/005-eth-near-sol-prospective-paper.md) paper test, its rule hash or the
`replay-v2.2` / `gate-v2.1` pins. No model calls, no network, no trading, no activation.

## Why

Setup-2 trades about once per 25 days per coin and market, so evidence accrues slowly. The
operator asked whether a smaller timeframe would help. The earlier
[perp-intraday timeframe study](perp-intraday-timeframes-research.md) found H1 worse than H4 for
Perp (507 vs 148 trades, Perp net −72 vs +38 USDT, fees 134 vs 48), but it used a different rule
(Donchian 30/8, EMA50 on H4/H8, no ADX/volume/profile) and left Spot on H4. It is therefore not a
test of Setup-2 at H1. This study is that test, kept apart from the frozen candidate.

## Candidate

Identical to Setup-2 except that every window counts H1 bars instead of H4 bars:

| Element | H4 control | H1 candidate |
|---|---|---|
| Donchian entry/exit | 30/10 H4 bars | 30/10 H1 bars |
| EMA / ADX / volume MA | 200/50, DMI-ADX14 > 20 rising, MA20×1.2 on H4 | same counts on H1 |
| Volume profile | prior 120 H4 bars (20 days) | prior 120 H1 bars (5 days), same 50 bins, VA 70% |
| Stop / trail / sizing | ATR14×3 on H4, 2% ATR sizing | same on H1 |
| Basket and risk | ETH/NEAR/SOL thirds, Spot 60 / Short 40, 1x, daily 3%, DD observe-only | unchanged |

Costs and funding are the study's existing assumptions (Spot 10/5 bps, Perp 5/5 bps per fill, actual
funding). No parameter is swept and no extra variant is added after seeing results.

Window `[2022-01-01, 2026-10-02)` UTC, using the same approved manifest as
[donchian-basket3](donchian-basket3.md) (SHA256 `fe09301c…b8a9`).

Cases (one run each, deterministic rerun and full-journal comparison as in the other studies):

1. `ETH-NEAR-SOL-H4`: frozen Setup-2 control. It must reproduce the published basket result.
2. `ETH-NEAR-SOL-H4-cost2x`: control with fee and slippage doubled.
3. `ETH-NEAR-SOL-H1`: candidate.
4. `ETH-NEAR-SOL-H1-cost2x`: candidate with doubled costs.

## Bar derivation

H1 bars are built from the stored native M15 candles (open of the first, extremes of the set, close
of the last, summed volume). Nothing is fetched or synthesised.

- Every M15 hour must be complete, except the audited 2023-03-24 Spot outage (five missing M15
  opens). That leaves one absent hour (13:00) and one partial hour (12:00, three quarters); the
  absent hour stays absent, like the M15 series.
- The same aggregation applied to M15 → H4 must equal the native H4 candles. Spot reproduces every
  complete H4 exactly. Perp differs at four audited windows (2022-07-16 04:00 volume only;
  2023-11-10 12:00 and 16:00; 2024-10-28 20:00) that the study already treats as native H4/M15
  disagreements. Any other disagreement stops the run.
- Warmup is 484 H1 bars (the M15 warmup the data holds), not the 600 bars of the H4 control. EMA200
  is seeded by SMA at bar 200 and has 284 further bars; ADX, ATR and volume MA are fully converged.
- The H1 candidate uses M15-derived bars in the four Perp windows above, where the control uses the
  native H4 candle. At most 16 H1 bars per coin are affected.

## Pre-registered decision

The candidate advances only if **all** hold (computed by `donchian_h1_verdict`, not by hand):

1. Net return at normal costs is not below the H4 control.
2. Maximum known drawdown is below 15% (the live portfolio halt).
3. Net return at doubled costs is positive.
4. Spot net PnL and Perp net PnL are both positive at normal costs.
5. Equity gains in both halves of the window, split at 2024-10-29 (start of the earlier 703-day
   research window).

Otherwise the conclusion is **keep H4**. Passing does not activate anything: the window is already
seen and the H4 rule was chosen on the same data, so a pass is only grounds to propose a new frozen
prospective test (ADR-007), which would need its own approval and rule hash.

## Limitations

- Seen data; selection among timeframes on it is exploratory, not out-of-sample evidence.
- Volume profile is approximate (M15 volume spread uniformly over each candle's range).
- M15 OHLC touch execution order inside a candle is unknown; gaps and exit costs can overshoot limits.
- Ideal fractional fills; no order book, partial fills, exchange filters or historical Demo parity.
- H1 candles in four Perp windows come from M15, not the native H1 feed.

## Run

```bash
uv run python -m intraday.replay_v2.donchian_adx_study --universe h1 \
  --inputs /home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-five-20261005/approved-data/inputs.json \
  --report-root /home/julius/.local/state/aigorithmic-trading/reports/donchian-setup2-h1-20261011
uv run python -m intraday.replay_v2.donchian_h1_verdict \
  /home/julius/.local/state/aigorithmic-trading/reports/donchian-setup2-h1-20261011/runs/comparison.json
```

Commit the clean implementation first: the runner binds the engine commit, source hashes, config
hashes and the input SHA256, and refuses a changed binding on resume.

## Result (2026-10-11)

Engine commit `b42b56c`, report root `donchian-setup2-h1-20261011` (private reports directory).
The control reproduced the published basket result exactly (`result_id`, dataset checksum and
summary equal for both H4 cases). Every case passed the deterministic double replay.

| Case | Final USDT | Return % | DD % | Trades | Fees + slippage | Spot net | Perp net |
|---|---:|---:|---:|---:|---:|---:|---:|
| H4 control | 1,817.45 | +81.75 | 11.58 | 423 | 200.8 | +493 | +324 |
| H4 cost2x | 1,573.90 | +57.39 | 12.71 | 423 | 371.0 | +322 | +252 |
| **H1** | 731.09 | **−26.89** | **45.92** | 1,725 | 514.8 | −93 | −176 |
| H1 cost2x | 350.34 | −64.97 | 68.88 | 1,725 | 743.0 | −377 | −272 |

All five registered criteria fail (return below control, DD above 15%, doubled costs negative,
Spot and Perp both negative, first half −392 USDT / second half +123 USDT). H1 gross price PnL is
still positive (+250 USDT) but costs of 515 USDT consume it; mean holding time falls from 68.8 h to
16.5 h and trailing-stop exits rise from 361 to 1,421. The filters block many more H1 breakouts
(ADX 2,393, volume profile 2,555, EMA200 1,267), yet the survivors still lose after costs.

**Conclusion: keep H4.** No ADR-007 and no new prospective test are proposed.
