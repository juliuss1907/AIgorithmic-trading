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

## Second round: four one-concept H1 candidates (pre-registered 2026-10-11)

The operator asked for the indicator set to be shrunk (EMA 50/20 with ADX20) and for further H1 setups.
Before choosing them the H1 baseline's trade log was read, because it shows where Setup-2 earns:

| | H4 control (423 trades) | H1 baseline (1,725 trades) |
|---|---:|---:|
| Held ≤ 48 h | 183 trades, net −1,030 | 1,689 trades, net −810 |
| Held > 48 h | 240 trades, gross +1,968, costs 113 | **36 trades**, gross +553, costs 12 |
| Median hold | 58 h | 14 h |
| Win rate / avg win / avg loss | 41% / 14.5 / −7.0 | 35% / 4.4 / −2.6 |

The long tail pays for everything, and H1 per-trade tail profit (~15 USDT) is not worse than H4 (~8).
H1 simply rarely reaches the tail: ATR14×3 on H1 is about half the price distance of ATR14×3 on H4, and
82% of H1 trades leave by trailing stop. Indicator size is not the lever: in clock time the H1
EMA200 is already 8 days against 33 days at H4, and a smaller EMA loosens the filter.

Each candidate changes one concept against the H1 baseline; all run at normal and doubled costs on the
same basket, window, bars and costs, against the same H4 control:

| Candidate | Change | Prediction (written before the run) |
|---|---|---|
| `H1-GEOM` | stop and trail ATR14×6, Donchian exit 40 bars (H1 ATR×6 ≈ H4 ATR×3 in price, 40 H1 bars = 10 H4 bars) | fewer trades, many more holds > 48 h, clearly better than baseline, probably still below H4 |
| `H1-REGIME` | EMA200/50 and ADX/DMI14 > 20 read from the last closed H4 bar; Donchian, volume, profile, ATR and stops stay on H1 | fewer trades, better than baseline |
| `H1-EMA50-20` | EMA50 as trend line, EMA20 as momentum line (close on the right side and rising), replacing EMA200/EMA50 | looser filter, more and shorter trades, worse than baseline; included to test "the indicators are too large" |
| `H1-ADX30` | ADX threshold 30 instead of 20, both markets | fewer trades, small improvement, still negative |

Mechanics worth knowing:

- Regime features use native H4 candles. For an H1 bar closing at time `t`, only the H4 bar with
  `available_at ≤ t` is used, so a still-open H4 candle never speaks for the regime. The entry
  event records `regime_available_at`.
- With EMA 50/20 the feature and blocker names are `ema_fast` / `ema_slow` (not `ema50` / `ema200`),
  because `ema50` would otherwise mean the slow line there and the fast line elsewhere.
- Position sizing stays `min(1, 0.02 / ATR%)` for every candidate. A wider stop therefore raises the
  loss per stopped trade; that is part of the geometry being measured, not corrected.
- The H4 control and the H1 baseline must reproduce `donchian-basket3-20261009` and
  `donchian-setup2-h1-20261011` exactly; this also proves the engine changes left defaults alone.

**Decision rule:** the same five criteria as above, computed by `donchian_h1_verdict` for each
candidate against the same H4 control. This is four further tries in one family on already-seen data,
made after the baseline failed, so a pass is weaker evidence than the first test and only grounds for a
new frozen prospective test. No further H1 variants are added without a new reason.

```bash
uv run python -m intraday.replay_v2.donchian_adx_study --universe h1-family \
  --inputs /home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-five-20261005/approved-data/inputs.json \
  --report-root /home/julius/.local/state/aigorithmic-trading/reports/donchian-setup2-h1-family-20261011
uv run python -m intraday.replay_v2.donchian_h1_verdict \
  /home/julius/.local/state/aigorithmic-trading/reports/donchian-setup2-h1-family-20261011/runs/comparison.json
```

### Result of the second round (2026-10-11)

Engine commit `9b85a29`, report root `donchian-setup2-h1-family-20261011`; 12 cases, each with a
deterministic double replay. The H4 control and the H1 baseline reproduced the earlier reports exactly
(`result_id` and summary), so the engine changes left defaults untouched. Peak memory 7.9 GB, 22 minutes.

| Case | Return % | DD % | Trades | Fees + slippage | Spot net | Perp net | Holds > 48 h (gross) | Return at cost ×2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H4 control | **+81.75** | 11.58 | 423 | 201 | +493 | +324 | 240 (+1,968) | +57.39 |
| H1 baseline | −26.89 | 45.92 | 1,725 | 515 | −93 | −176 | 36 (+553) | −64.97 |
| H1-GEOM | **+63.26** | 24.70 | 1,205 | 703 | +604 | +28 | 500 (+5,810) | −1.57 |
| H1-REGIME | −17.21 | 35.47 | 905 | 286 | −139 | −33 | 20 (+334) | −42.50 |
| H1-ADX30 | −12.96 | 41.63 | 997 | 308 | +15 | −144 | 20 (+308) | −42.94 |
| H1-EMA50-20 | −33.70 | 49.50 | 1,764 | 501 | −146 | −191 | 36 (+518) | −68.71 |

**No candidate advances.** `H1-GEOM` fails return-not-below-control (63.3% vs 81.7%), drawdown below
15% (24.7%) and doubled costs (−1.6%); it passes the Spot-and-Perp and both-halves criteria
(first half +465 USDT, second half +168 USDT). The other three fail all but none or one criterion.

What the round shows:

- **The diagnosis held.** Letting H1 trades breathe (ATR×6, 40-bar exit) turns −26.9% into +63.3% and
  lifts holds beyond 48 h from 36 to 500. Every prediction was right in direction: GEOM best, REGIME
  and ADX30 better than baseline but still negative, EMA 50/20 worse than baseline.
- **Smaller indicators did not help.** EMA 50/20 produced more trades (1,764 vs 1,725) and the
  worst result of the family, as predicted; "indicators too large" is not the cause.
- **H1 still does not beat H4.** GEOM's gross price PnL (about +1,336 USDT) exceeds the control's
  (about +1,027), but it needs 1,205 trades against 423, so costs are 703 against 201 USDT and
  drawdown doubles. Wide stops also make the 705 trades held under 48 h lose heavily. Its Perp
  sleeve earns only +28 USDT; the result is carried by Spot.
- **Cost sensitivity is the binding constraint.** Only the control survives doubled costs
  comfortably; GEOM breaks even at best.

**Conclusion: keep H4; no ADR-007 and no new prospective test.** Setup-2 H1 would need a lever that
keeps GEOM's gross while cutting its trade count by roughly two thirds. Whether a combination of
geometry with a regime or ADX gate does that is a fifth and sixth look at the same seen data, so it
is left as an explicit decision for the operator rather than added here.

## Recent-window replay: 2026-01-01 → 2026-10-02 (pre-registered 2026-10-11)

The operator asked to look only at data from the start of 2026. The same 12 cases are replayed from
a fresh 1,000 USDT account at 2026-01-01, with the earlier rows used only to warm the indicators
(H4 600 bars, M15 484 hours, as before). `donchian_adx_study --start 2026-01-01` slices the frozen
candles after decoding; nothing is refetched.

- **Data end:** 2026-10-02, the end of the frozen manifest, not today. Newer candles need a new
  manifest and are out of scope here.
- **Criteria:** the same five. The two-halves test splits at the window midpoint, 2026-05-18, instead
  of 2024-10-29 (`donchian_h1_verdict.split_for`); this rule is fixed before the run.
- **Not independent evidence:** this window is part of the 2022–2026 window already used to pick and
  judge every candidate. It describes the recent regime only.
- **Thin sample:** Setup-2 H4 trades roughly once per 25 days per coin and market, so expect on the
  order of 60–100 trades; differences between candidates will be mostly noise. Read it descriptively.
- **Reference from the continuous run:** H4's two 2026 half-year segments were +3.6% and +10.2%
  (segments carry capital and positions, so a fresh account will differ slightly).

```bash
uv run python -m intraday.replay_v2.donchian_adx_study --universe h1-family --start 2026-01-01T00:00:00+00:00 \
  --inputs /home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-five-20261005/approved-data/inputs.json \
  --report-root /home/julius/.local/state/aigorithmic-trading/reports/donchian-setup2-h1-family-2026ytd-20261011
```

### Result of the recent-window replay (2026-10-11)

Engine commit `1c67af1`, report root `donchian-setup2-h1-family-2026ytd-20261011`; 12 cases from a fresh
account at 2026-01-01 to 2026-10-02, each with a deterministic double replay (5 minutes, 6.9 GB).

| Case | Return % | DD % | Trades | Fees + slippage | Spot net | Perp net | Return at cost ×2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **H4 control** | +14.23 | 10.46 | 65 | 26 | +86 | +56 | +11.38 |
| H1 baseline | +8.12 | 11.03 | 265 | 117 | +124 | −43 | −3.79 |
| H1-GEOM | +4.63 | 21.10 | 192 | 85 | +81 | −34 | −3.63 |
| H1-REGIME | +8.12 | **7.36** | 140 | 62 | +40 | +41 | +1.83 |
| **H1-ADX30** | **+15.50** | 9.80 | 140 | 64 | +145 | +10 | +8.53 |
| H1-EMA50-20 | +6.84 | 11.23 | 270 | 118 | +116 | −47 | −5.12 |

Mechanical verdict (split 2026-05-18): **`H1-ADX30` meets all five criteria**; `H1-REGIME` fails only
the return criterion (8.1% vs 14.2%); baseline, GEOM and EMA 50/20 fail four or five.

Why this is not grounds to change anything:

- **The margin is noise.** ADX30 beats H4 by 12.7 USDT (+1.3 pp). Per-trade results give a standard
  error of about 150 USDT on that difference, and about 113 USDT on H4's own +142. The window cannot
  separate ADX30 from H4, nor reliably H4 from zero.
- **Selection.** ADX30 is one of five candidates, picked after the full-window run showed it at −13.0%
  (DD 41.6%). Taking the best of five in a sub-window of the same data is what chance alone produces.
- **Different mechanism.** In this window ADX30 holds only 2 trades beyond 48 h (median hold 14 h)
  against H4's 38 (median 58 h); it earns through frequent short Spot trades with 2.2× the trades
  and 2.5× the costs, and the second half carries almost all of it (+10.8 then +144.2 USDT).
- **GEOM reverses.** The full-window winner among H1 variants falls to +4.6% with 21% drawdown here;
  the long-trend years it relied on are mostly outside 2026.
- **H4 is the stable reference.** +14.2% at 10.5% drawdown on 65 trades, +11.4% at doubled costs, and
  the same sign in both halves, consistent with its earlier segments.

Per the pre-registered rule a pass is at most grounds to propose a new frozen prospective test.
The recommendation is not to, because no statistical separation exists and the mechanism differs
from the one Setup-2 was chosen for. ADR-005 (ETH-NEAR-SOL H4) stays the only frozen candidate.
