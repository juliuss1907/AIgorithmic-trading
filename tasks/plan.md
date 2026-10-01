# Plan hiện hành — AIGT multi-asset và execution modules

Cập nhật 2026-10-01; implementation baseline `29f05c8`, source schema v23.
[Kiến trúc chuẩn](../docs/crypto-intraday-system-design.md) ·
[Roadmap](roadmap.md) · [Checklist](todo.md).

## Mục tiêu và hợp đồng hiện hành

- Dynamic catalog, lifecycle/rule/evaluation theo `(symbol, scope)`; add/scan/select riêng Spot/Perp.
- Spot trigger UTC 4h, native 8h/1d context; dashboard history UTC+7.
- Perp Jev primary 30s; LLM hourly evidence analysis và daily eligibility-based bounded proposals.
- Rule/risk deterministic; không auto-promote, auto-activation hoặc auto-rebalance.
- Decision-only soak không tạo fill. Simulator paper và Binance Demo là execution paths khác nhau.
- Demo supports USDT Spot/USD-M Perp của coin catalog mà sàn hỗ trợ; source read-only,
  execution journal/credentials riêng. Binance live/Hyperliquid execution chưa có.
- Multi Perp default isolated 3x, verified từng cặp 1–10x qua Confirm/read-back.
  Legacy BTC Demo fixed 3x. Spot emergency stop 10%, Donchian exit riêng.
- Shared Demo allocation 60/40 budget, effective caps 30/20% operator capital; actual margin
  và loss guards theo runtime. Không dùng các tranche/margin limits standalone cũ cho Demo.

## Các phase nền tảng đã hoàn thành

Các số phase bên dưới giữ lineage của kế hoạch multi-cadence trước đây:

1. System Plan và thiết kế nền.
2. Scheduler đa nhịp, market cache, deterministic risk.
3. Compact shadow A/B contracts và paired evidence.
4. Forward outcome engine.
5. A/B evaluation và deterministic retrospective.
6. Hourly thesis/daily proposal, manual rule lifecycle.
7. Dashboard/doctor/provider operations.
8. Operator API và Hermes distribution, rollout add-on riêng.
9. Unified source-dashboard/legacy routes.
10. Read-only soak readiness report và private artifact.
11. `aigt positions` cho **BTC simulated parent**, chưa cho Demo.

Các phase này mô tả code đã xây; không khẳng định tất cả jobs/add-ons đang chạy trong soak mode.

## Phần mở rộng đã xây

- Multi-symbol collectors/catalog, schema v20→v23 compatibility và bảo toàn evidence.
- Generalized baseline/proposal/lifecycle cho Spot 4h và Perp, không còn ETH-only.
- Binance Demo execution modules đa coin; separate journal, durable unknown reconciliation,
  native protection, owned-only Spot inventory và shared allocation.
- CLI/dashboard onboarding và confirmed per-pair Perp controls; profiles opt-in.
- Fake-exchange/security/regression/browser checks có evidence trong các feature plans.
  Không thay thế credentialed Demo order/settings acceptance.

## Trình tự gate vận hành

**Spot:** ≥365 ngày replay history → passing replay ID → operator start soak →
≥14 ngày/đủ heartbeat/setup/outcome → passing soak → promote → execution activation riêng.

**Perp bootstrap:** decision soak ≥14 ngày/100 outcomes/95% coverage →
pre-cutoff replay → post-replay validation ≥72 giờ/100 outcomes/95% →
promote → activation riêng. BTC Perp legacy giữ exception portfolio evaluation của nó.

Không bắt đầu Spot soak khi replay reject, không copy BTC pass sang coin khác.
Replay Spot hiện chỉ Donchian next-open/costs, chưa full Demo risk parity.

## Rollout đã ghi nhận và việc tiếp theo

Snapshot VPS 2026-10-01 05:30 UTC: worker/web baseline `29f05c8`, v23, health OK;
BTC + 5 Perp tiếp tục soak, campaign starts giữ nguyên; BTC Spot 30/8 replay reject;
Demo không bật. Snapshot có thể stale, xem report mới trước action.
Nguồn: `XDG_STATE_HOME/aigorithmic-trading/reports/vps-rollout-20261001T043018/final-health.json`.

Việc vận hành còn mở trong checklist: Perp pre/post-replay gates, Spot passing candidate,
supervised Demo acceptance, Hermes/Telegram read-only rollout và compact eligibility.
Per-asset readiness CLI/API/dashboard đã xây local; chưa deploy, không cấp quyền trading.
Chi tiết tại [readiness plan](asset-readiness-plan.md).
Backlog build theo roadmap: replay risk parity, unified Demo positions,
automated backup/alerts, rồi adapter Hyperliquid.

## Đợt đồng bộ tài liệu 2026-10-01

- [x] Chốt scope toàn bộ README/design/roadmap/runbooks/System Plan/diagram.
- [x] Đồng bộ code-truth và giữ historical evidence, không xóa open tasks.
- [x] Đồng bộ JSON/HTML/DOT và kiểm tra browser; xuất lại PDF/DOCX/PNG.
- [x] Validate links/CLI/help, regression, review; không đổi runtime hoặc source data.

[Biên bản kiểm chứng](../docs/documentation-refresh-2026-10-01.md).

Đợt này chỉ tài liệu/template/generated artifact/tests; không key/order/activation/VPS write.
Không push/merge/redeploy cho tới khi operator yêu cầu riêng.
