# Checklist multi-cadence AI paper trading

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

## Sau khi build

- [ ] Clone/cài repo lên VPS trước khi tích hợp Hermes.
- [ ] Tạo Telegram bot riêng cho `trading-ops`; tách Unix user trước khi bật actions.
- [ ] Chạy Hermes read-only 72 giờ; chỉ sau đó mới bật pause/resume.
- [ ] Chạy paper soak liên tục 72 giờ.
- [ ] Thu thập ít nhất 14 ngày và 1.000 numeric/compact pairs.
- [ ] Đánh giá compact eligibility; không auto-activate.
- [ ] Theo dõi rule challenger đủ 72 giờ trước manual activation.
- [ ] Chỉ push khi chủ dự án yêu cầu.
