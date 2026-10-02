# BTC/ETH gate v2 — local verification 2026-10-02

Scope: implementation và isolated snapshot evaluation. Không push/merge/redeploy,
không write VPS, start validation, promotion, exchange setting/order hoặc gọi model.
[Plan](../tasks/btc-eth-gate-v2-plan.md) · [Operator runbook](replay-v2-runbook.md).

## Source và artifact bindings

Existing VPS snapshot local: `/tmp/aigt-four-coin-replay-mlOHG8/source.sqlite3`.
Research cutoff **2026-10-02T02:00Z**, không phải live/current VPS state.
Raw SHA256 trước/sau: `e0b6586d79b7cc5aace31d6fb065c141425050ce8f795e6c2514031c0488b825`.

Root: `/tmp/aigt-btc-eth-gate-v2-ni3n9fw8`.
Consistent SQLite baseline checksum:
`d61ee8fa10ddc32274540b60985d04c0fa15285690da83d3a32d061c3a99d119`, integrity `ok`.
Final evidence backup:
`evidence/intraday-20261002T061841484201Z.sqlite3`, 554102784 bytes, integrity `ok`,
SHA256 `143bdc44fb8f05fee7ba101255e8b606d4c9dadfa4d3a0f66d0511116f470bf3`.
Reports dưới `reports/`, bundles kiểm chứng bằng `read_report` checksums.
Working DB là bản sao local; tuyệt đối không dùng evaluation IDs dưới đây trên live source.

## ETH Spot — formal gate pass trong isolated source

- Candidate `ethusdt-spot_4h-candidate-ddead073e86ae66a7677`, Donchian **30/8**.
- Evaluation `cb5a50ea7eacf10443b816726cefcc45`, `gate-v2.1`, **pass**.
- Run `cf287d08569e4c099c13636591001f16`, accounting `replay-v2.2`.
- Window 2026-03-31T12:00Z → 2026-10-02T00:00Z; 2202 bars, coverage 100%.
- Net sau phí **+2.239644%**, DD **3.777306%**, **10 closed trades**,
  hard-risk violations 0; fee 5.830984, slippage 2.915492 USDT trên vốn 1000.
- Không start soak hoặc đổi legacy rejection. Khác research holdout +3.163621%
  vì gate dùng window cố định của source, không phải selection/holdout window.
- Limitations vẫn hiển thị: ex-post, Spot Jev chưa replay, adverse-first OHLC,
  idealized instrument filters và market prices chưa xác minh historical Demo quotes.

## BTC Perp — inherited collection, gate deferred

- Champion `perp-rule-v1`, hash
  `12a6d9274d638f172d3b8bee3b71b5ec70cd1c8a9a9c898557cbd44feeef68c5` giữ nguyên.
- Candidate local `btcusdt-perp-v2-d67ccd7c68eab9ae925b`; creation clock là cutoff
  của phép thử isolated, không rewrite timestamp của champion/collection cũ.
- Collection from **2026-09-25T12:55:46.676811Z**; audited prefix tới
  2026-10-02T01:45Z, **17292 verified decisions**, 17438 quotes.
- Prefix checksum `9b939526b8a76b3fad32198b9108e802c5f0c791e0216e68c07d136554ee3b01`.
- Evaluation `eafb2d31993c6a3cd71c6b53ee83ff7e`, run
  `846c4cd51e61418d82a99a3f8dc0638f`, **deferred**, không gọi là pass.
- Growing window: **6.544599 ngày**, 17322 verified decisions, 17257 matured outcomes;
  outcome coverage 99.624755%, heartbeat **91.866943%**, quotes **92.641519%**.
- **0 closed trades**. Funding chưa cung cấp nên net return null, không giả funding 0.
- Blockers: minimum 14 days, heartbeat/quotes ≥95%, funding complete,
  minimum 6 closed trades và champion metrics complete.
- Report còn `perp_quote_history_gap`; entries bị lọc vì confidence/entry quality,
  toxic flow/model risk hoặc stale decision. Không hạ threshold để ép pass.
- 14-day time bound từ mốc collection: **2026-10-09T12:55:46.676811Z**
  (19:55:46 UTC+7), chỉ là earliest time, không hẹn pass khi còn blockers khác.

## Safety và regression

- `uv run pytest -q`: **886 passed**, 18 upstream deprecation warnings, 49.63s.
- Focused collection/gate CLI/repository/lifecycle/Perp E2E: **24 passed**, 29.50s.
- `uv build`: wheel và source distribution thành công.
- `uv run aigt assets rules inherit-perp --help`: CLI interface đúng.
- `git diff --check`: pass.

TDD chứng minh inherited candidate không được fallback legacy start/replay;
raw source giữ checksum, v1 registry/champion không đổi. Real-engine test collection
14 ngày và validation 14 ngày dùng disjoint outcomes; trước validation maturity
outcomes 0 và status deferred; không auto-promote. Optional readers không tạo DDL.

Baseline/final comparison giữ nguyên mọi original scoped-rule row, lifecycle,
asset/v1 registries, venue routes và v1 evaluations; không thêm validation campaign.
Raw table counts giữ nguyên: signals 46657, outcomes 138678, snapshots 64450,
model calls 47411, portfolio ticks 46887. Final backup và hai report bundles được
kiểm chứng lại checksum sau mọi thao tác isolated.

SOL daily-loss/terminal-halt investigation được ghi backlog, chưa đổi ledger policy.
