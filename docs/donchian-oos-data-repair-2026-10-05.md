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
