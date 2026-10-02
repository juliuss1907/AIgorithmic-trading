# BTC Perp collection inheritance và ETH Spot gate v2

Approved 2026-10-02. Local implementation/verification only; no push, merge,
VPS redeploy, validation start, promotion or Demo activation in this change.

## Quyết định và checklist

- [x] BTC: explicit `assets rules inherit-perp`, clone nguyên tham số champion.
- [x] Binding immutable candidate/champion hashes + audited source window/checksum;
  actual creation clock không bị backdate. Không đổi champion/registry anchor v1.
- [x] Collection v2 được tiếp tục từ mốc cũ đã audit; gate vẫn yêu cầu 14 ngày,
  provenance/funding, coverage, số mẫu và account replay. Không copy v1 pass.
- [x] Validation mới vẫn dùng campaign start riêng; collection cũ không được tính.
- [x] CLI/status/readiness phân biệt collection và post-gate validation; legacy
  lifecycle không được fallback cho candidate đã chọn collection v2.
- [x] ETH 30/8 chạy gate v2 chính thức trên isolated snapshot, lưu evaluation/report.
- [x] Giữ risk daily loss 1.5%, DD 8%, Spot stop 10%; không đổi confidence/rule tuning.
- [x] Regression và checksum/source-preservation checks có evidence trong
  [biên bản kiểm chứng](../docs/btc-eth-gate-v2-verification.md).
- [ ] Sau approval riêng: push/redeploy CLI/admin/web và chạy lại gate trên VPS.
- [ ] ETH: nếu fresh VPS gate pass, xin operator xác nhận start soak mới ≥14 ngày.
- [ ] BTC: tiếp tục collection; replay lại khi đủ thời gian/evidence/funding/trades.
- [ ] Promotion và Demo acceptance/activation là quyết định riêng, không tự động.

## SOL — deferred, chưa đổi policy

Nguồn: study VPS `ecc989ac56694e51ab5d1c781bc8fef3`, training 18 tháng trong bộ
24 tháng; không có passing training candidate nên chưa chạy holdout.

| Setup | Net sau phí | DD | Closed trades | Kết quả |
|---|---:|---:|---:|---|
| 30/8 | -0.186% | 2.668% | 3 | reject |
| 40/8 | +1.295% | 2.130% | 2 | deferred |
| 50/8 | +1.295% | 2.130% | 2 | deferred |

40/8 và 50/8 có một Donchian exit và một daily-loss exit. Replay giữ terminal
`halted` tới hết run; UTC day rollover chỉ cập nhật day-start equity, không resume.
Chưa đủ minimum 6 closed trades; thêm history không tự khắc phục terminal halt.

- [ ] Chốt riêng policy daily pause đến ngày UTC kế tiếp (07:00 Việt Nam), còn
  max-DD giữ terminal/operator halt. Hiện chỉ là đề xuất, chưa được triển khai.
- [ ] So sánh 1.5%/3% trên cùng reset policy, dataset/window/setup/costs; không
  thay nhiều biến để ép pass, không tự hạ gate hoặc chọn từ holdout.
- [ ] SOL/NEAR/ZEC giữ nguyên lifecycle và rule trong đợt BTC/ETH này.

## Rollback

Extension collection là additive trên source v23. Không xóa/rewrite v1 tables,
signals, model calls, rule hashes hoặc evaluations. Không tự drop extension hay
binding để mở lại v1 fallback. Khi rollout mới chưa được chấp thuận, VPS giữ nguyên.
