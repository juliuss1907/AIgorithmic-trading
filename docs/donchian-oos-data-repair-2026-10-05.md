# Approved warmup timestamp correction — 2026-10-05

Operator explicitly approved conservative closeTime normalization of exactly
three Spot H4 source rows:BTCUSDT,ETHUSDT,SOLUSDT at2021-09-29T04:00UTC.
Original openTime1632888000000,closeTime1632898799999 (3h); normalized
closeTime1632902399999 (4h). This is warmup metadata, not an active trading
bar or OHLCV repair. No prices, volumes, missing bars or other columns change.

Policy ID:`approved-20210929-spot-h4-warmup-closeTime-v1`.
Raw diagnostics and SHA256 remain immutable. Normalized snapshots are new
derived artifacts. `repairs.json`, bundle and QA retain original/normalized
row SHA256, source-file SHA256, snapshot ID and changed field. No generic
short-bar normalization: different coin/time/closeTime, duplicates or any
additional timestamp irregularity must fail. Native M15 is not interpolated.

CLI opt-in is explicit and bound to the same-source diagnostic audit:

```bash
uv run python -u -m intraday.replay_v2.donchian_oos_data \
  --seal /absolute/new-batch/freeze-02.json \
  --approved-warmup-audit /absolute/diagnostics/native-timestamp-audit.json \
  --output-root /absolute/new-batch/data-02
```

Re-seal reviewed data-handling code before collecting other series. Preserve
the original freeze/reference/failed batch. Engine signals, risk, costs,
strategy checksum and machine criteria must remain identical to freeze01.
Only data-handler source and this approved policy are new; no OOS results
have been viewed. Completed QA is still required before seven-case replay.

## Approved source-preserving Spot M15 availability policy

Operator additionally approved handling the audited source gap on2023-03-24:
five missing native Spot opens12:45,13:00,13:15,13:30,13:45UTC for BTC/ETH/SOL.
Policy:`binance-spot-m15-audited-20230324-v1`. No missing candles, prices or
volume are created. The preceding12:30 partial row retains its actual source
closeTime; BTC/ETH also retain audited partial warmup rows at2021-12-24 04:45.
Other missing/duplicate/irregular bars remain errors. Generic native validators
remain strict; only an explicit, checksum-bound Spot M15 snapshot opts in.

Spot stops/Donchian/daily exits cannot fill during unavailability. A pending
exit waits for the next actual Spot open, priced there; ATR gap checks retain
their ordinary priority. New entries are not queued from stale signals.
Independent validated Perp/mark/funding events continue. Each valuation sample
records last Spot observation times and stale symbols; report summaries disclose
the missing opens and stale sample count. DD remains known-sample DD, not a
claim about unobserved prices; daily3% cannot guarantee a capped realized loss.
Volume profile uses only actual observed rows in the same prior20-day window,
with no expansion, interpolation or invented volume. Buy-and-hold valuation
merges actual close events and carries the last observed price per coin.

Explicit collection option:`--approved-spot-gap-audit /absolute/diagnostics/native-m15-audit.json`.
Original raw files, failed batches, previous freezes and reference are retained.
Reproduce six old golden full journals, commit and create freeze03 before new
collection. Engine event scheduling/data handling change, not strategy parameters,
costs, sizing, risk rules or scoring criteria. No fresh OOS strategy PnL was
inspected before this approved technical correction.

## Approved single native mark-price gap

Operator subsequently approved last-observed mark valuation for the single
missing native M15 mark bucket at2023-11-10T03:45UTC (10:45UTC+7),BTC/ETH/SOL.
Same-endpoint full audits each have99055 rows,no duplicates/irregular closes;
exact-window retries return[]. Policy:`binance-mark-m15-audited-20231110-v1`.
No inferred mark OHLC or synthetic candles. Last actual observed mark remains
in equity/collateral/risk valuation; every sample labels stale symbols and
actual last observation times. At04:00UTC the next actual mark replaces it.
Actual trade-price bars still drive ATR fills; actual funding remains strict
and independent. DD/daily risk are known-price observations, not claims about
unobserved mark moves. Another mark gap/duplicate or source anomaly still fails.

Explicit opt-in:`--approved-mark-gap-audit /absolute/diagnostics/native-mark-audit.json`.
The raw diagnostic files and SHA256 bind new mark snapshots. Existing completed
native checkpoints may be reused read-only via`--reuse-root`; an immutable
reuse-file manifest verifies source hashes/identity/coverage and binds resumes.
No failed batch or previous freeze is overwritten. Reference04 must reproduce
legacy ledgers before freeze04/new collection. Strategy and criteria remain
unchanged; no fresh strategy PnL has been viewed.

## Approved funding price references, not original settlement quotes

The actual funding API preserves rates/times but returns empty`markPrice` for
2005 BTC and ETH settlements and2080 SOL settlements from2022-01-01 through
2023-10-31. All have an actual native M15 mark open0–31ms before settlement.
Operator explicitly approved that OPEN price as a reference, not an API-confirmed
settlement quote. Policy:`approved-funding-native-mark-open-31ms-v1`.

Original raw rows retain empty marks, original rates/timestamps and source SHA256.
Derived snapshots record every reference funding time, actual complete source
mark row/row hash, source snapshot ID and age. Decoder cross-checks them against
the actual native mark series. Never replace an existing API quote, use a later
close/high/low, accept a future or older-than31ms open, invent funding rates,
or weaken shared FundingSnapshot validation. Unknown quote periods still fail.
Ledger events, input/audit metadata, summary and limitations disclose the reference
pricing assumption. Known cost/PnL is not exact historical account reconciliation.
Funding coverage remains strict: observed largest interval8h+28ms is below8h+1s.

Opt-in:`--approved-funding-reference-audit /absolute/diagnostics/native-funding-audit.json`.
Requires the checksum-bound native mark audit. Reverify six legacy journals,
review/commit and freeze05 before collecting the new derived bundle. Preserve all
old freezes/failed batches; no fresh strategy PnL has been inspected. Strategy,
fees/slippage assumptions and scoring criteria remain unchanged.

## Exact approved native H4/M15 price-view differences

Operator approved exactly five disagreements after the full active-window
boundary audit, before any OOS strategy performance was inspected. No price
normalization, reconstruction or generic epsilon. Native H4 indicators and
native M15 fills retain their own original source values.

| Market | Symbol | H4 open UTC | Field | H4 | M15 |
|---|---|---|---|---|---|
| Spot | BTCUSDT | 2023-03-24 12:00 | open | 28079.99 | 28080.00 |
| Spot | ETHUSDT | 2023-03-24 12:00 | open | 1789.51 | 1789.52 |
| Perp | BTCUSDT | 2023-11-10 12:00 | close | 37118.40 | 37092.60 |
| Perp | ETHUSDT | 2023-11-10 12:00 | close | 2085.32 | 2091.11 |
| Perp | SOLUSDT | 2023-11-10 12:00 | close | 51.1510 | 50.9140 |

Spot exceptions are part of the explicitly approved audited Spot source policy.
Perp additionally requires`--approved-native-boundaries`; policy ID
`approved-native-boundary-prices-2023-v1` binds bundle/checkpoint/seal and engine
dataset identity. All other fields/bars/coins/times retain exact native boundary
validation. Missing bars, alternate discrepant prices, unknown policies or
tampered originals still fail. Existing default validators remain unchanged.
Spot availability metadata, Perp QA/summary and limitations disclose these views.
The separate one-bar mark gap and funding reference pricing approvals remain
in force. Commit and freeze06 after tests/review and legacy-ledger verification.
