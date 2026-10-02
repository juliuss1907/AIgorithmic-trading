# Checklist AIGT hiện hành — build, rollout và nghiệm thu

Cập nhật 2026-10-01, baseline `29f05c8`, schema v23.
[Kiến trúc chuẩn](../docs/crypto-intraday-system-design.md) · [Roadmap](roadmap.md).
Checkbox code không thay cho quyền activation hoặc bằng chứng vận hành.

## Đang triển khai

- [x] Phase 0: commit trang System Plan và tài liệu kế hoạch.
- [x] Phase 1: scheduler đa nhịp, cache dữ liệu và risk loop không gọi AI.
- [x] Phase 2: compact Jev shadow A/B, journal variant/mode/pair.
- [x] Phase 3: forward outcome engine cho Perp và Spot.
- [x] Phase 4: evaluator A/B, eligibility gate và retrospective 09:00.
- [x] Phase 5: evidence-aware LLM thesis và manual rule lifecycle.
- [x] Phase 6: dashboard vận hành, doctor và runbook.
- [x] Phase 7a: Operator API, token scopes, action approval, audit và alert cursor.
- [x] Phase 7b: Hermes `trading-ops` distribution, read tool, slash commands và cron scripts.
- [x] Phase 8: global `aigt` deployment registry, Docker routing và safe setup.
- [x] Phase 9: unified dashboard đọc journal/soak hiện tại, API signal đã redact và legacy route.
- [x] Phase 10: soak readiness report read-only, JSON artifact và global Docker routing.
- [x] Phase 11: `aigt positions` cho open Spot/Perp paper positions và unrealized P&L.

## Sau khi build

- [x] Repo/core đã cài và deploy lên VPS; Hermes add-on vẫn là rollout riêng.
- [ ] Tạo Telegram bot riêng cho `trading-ops`; tách Unix user trước khi bật actions.
- [ ] Chạy Hermes read-only 72 giờ; chỉ sau đó mới bật pause/resume.
- [ ] Chạy simulator paper acceptance liên tục 72 giờ; decision-only soak pass không chứng minh fill/reconciliation.
- [ ] Thu thập ít nhất 14 ngày và 1.000 numeric/compact pairs.
- [ ] Đánh giá compact eligibility; không auto-activate.
- [ ] Theo dõi rule challenger theo đúng scope gate: Spot ≥14 ngày; Perp ≥14 ngày trước replay + ≥72 giờ validation.
- [ ] Chỉ push khi chủ dự án yêu cầu.

## Multi-asset/Demo đã xây

- [x] Dynamic catalog v23, independent Spot/Perp add/scan/select.
- [x] Generalized Spot 4h và Perp baseline/proposal/lifecycle.
- [x] Binance Demo đa coin, allocation/risk và execution journal riêng.
- [x] Per-pair leverage CLI/dashboard confirmation/controller opt-in.
- [x] Readiness theo symbol/scope và blockers pre/post-replay: CLI/API/dashboard, read-only v23 (local).
- [x] Replay v2 research Spot/Perp: offline ledger/risk/assumptions, CLI, private artifacts và read-only web UTC+7 (local, chưa deploy).
- [x] Versioned gate v2 và separated Binance Regular User costs; explicit campaign mới, giữ v1 evidence (local, chưa deploy).
- [x] VPS source worker/web v23 rollout, bảo toàn evidence (snapshot 2026-10-01 05:30 UTC).

## Nghiệm thu và vận hành còn mở

- [ ] Hoàn tất 5 Perp decision soak ≥14 ngày/100 matured outcomes/coverage.
- [ ] Perp replay cutoff và validation độc lập: v1 ≥72 giờ; campaign v2 ≥14 ngày mới trước promotion.
- [ ] Spot candidate pass replay rồi operator start soak ≥14 ngày và đủ setup.
- [ ] Supervised Demo order → native protection → reconcile → close, approval riêng.
- [ ] Demo per-pair settings write/read-back acceptance khi paused/flat.
- [ ] Rollout/kiểm chứng per-asset readiness trên VPS; approval deploy riêng.
- [ ] Chỉ activate route đúng champion/passing evaluation sau acceptance được phê duyệt.

## Backlog build — chưa triển khai

- [ ] Deploy/accept versioned gate v2 trên VPS sau verified backup; evaluator đã xây local, không rewrite v1 evidence.
- [ ] Unified multi-coin Demo positions/orders/protection projection.
- [ ] Scheduled backup/retention/restore drill và Demo operational alerts.
- [ ] Hyperliquid execution adapter; live/multi-venue allocation cần spec riêng.

## Đồng bộ tài liệu hiện hành

- [x] README/design/roadmap/runbooks phân biệt current, legacy và historical.
- [x] System Plan/JSON/HTML/DOT và PDF/DOCX/PNG theo cùng code-truth.
- [x] Link/CLI/render/browser/regression checks pass; không runtime/data/secret change.

[Biên bản kiểm chứng](../docs/documentation-refresh-2026-10-01.md).
