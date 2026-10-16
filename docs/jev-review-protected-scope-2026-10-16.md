# Jev Setup-2 review: ngoại lệ nguồn cần duyệt

Ngày: 2026-10-16. Trạng thái: **Julius đã duyệt ngoại lệ đúng ba file bên dưới**.

Phê duyệt trong hội thoại ngày 2026-10-16: mặc định tắt giữ hành vi/output từng byte;
10/10 result_id và summary tham chiếu khớp; 96 file còn lại nguyên hash; ghi manifest
trước/sau của ba file; hỏi lại nếu cần sửa thêm file bảo vệ. Không merge vào
`feature/slot-runtime`, không deploy trước khi đánh giá soak 28/10 xong. Phê duyệt
không bao gồm gọi Jev thật; vẫn dừng sau dry-run.

Yêu cầu gốc: `/home/julius/julius-workspace/tasks/inbox/note-2026-10-16-1102.md`.
Worktree đã tạo đúng lệnh yêu cầu: `../system-trading-jev-review`, nhánh
`feature/jev-setup2-review`, base `9eeb207442cef7aada3ad3fe0aaccf2d47bc5f14`.

## Điểm cần quyết định

Ghi chú yêu cầu giữ nguyên hash source được bảo vệ và ghi rõ:
“nếu buộc phải sửa file nằm trong danh sách đó, dừng lại và hỏi”.
Trong khi đó, mục 5 yêu cầu thêm lớp phủ vào `engine.py::_setup2`.
File này được bảo vệ trong receipt mà báo cáo advisory dẫn tới. Hai file chứa
kiểu dataset và phép tính fingerprint cũng được bảo vệ.

Đã kiểm bằng SHA-256 trên worktree mới: **99/99 file khớp** manifest cũ.
Không diễn giải quyền thêm lớp phủ thành quyền bỏ ràng buộc bảo vệ nguồn.

## Ngoại lệ tối thiểu đề xuất

Chỉ cho phép thay đổi ba file được bảo vệ sau cho đường replay Setup-2 có bật lớp phủ:

| File | Thay đổi dự kiến | SHA-256 hiện tại, khớp manifest |
|---|---|---|
| `intraday/replay_v2/contracts.py` | Thêm trường dataset tùy chọn cho lớp phủ A đã đóng băng, mặc định `None`; kiểu và validation mới đặt trong module mới khi phù hợp | `b04e2de48ba300ad95e0691efd4c3504286309bd1cf2bc7ead0b66cc998ff546` |
| `intraday/replay_v2/engine.py` | Khi vào lệnh trong `_setup2`, tra `(rule_id, signal.bar_close)`, nhân target với hệ số A; hệ số 0 ghi `jev_review_veto`; tắt lớp phủ đi theo nhánh hiện có | `7494b3fecc183cee3983930003cfd358009d2ed3fabd16edfed89e56b4467640` |
| `intraday/replay_v2/metrics.py` | Chỉ đưa danh tính lớp phủ, phiên bản cấu hình và hash tập câu trả lời sử dụng vào fingerprint khi trường lớp phủ được đặt | `810d3f88d5bb92fa678473605993b744304e5ad54b8e3df9f2a0de9db00f6dd6` |

Các phần encoder, câu hỏi/parser, cache, collect và đối chiếu luật đơn độc sẽ
đặt trong module mới hoặc file không nằm trong danh sách bảo vệ. Không đổi
`DecisionScope`, schema database, rule, active set, soak hay runtime Demo.
Nếu cần sửa thêm file được bảo vệ, phải xin ngoại lệ mới.

Điều kiện nghiệm thu vẫn giữ: tắt lớp phủ thì 10/10 `result_id` và summary
tham chiếu khớp; 96 file được bảo vệ còn lại giữ nguyên hash; test lớp phủ
bật/tắt và toàn bộ pytest theo ghi chú. Đây là điều kiện sẽ kiểm sau triển khai,
**chưa phải kết quả kiểm chứng**.

## Bằng chứng

Manifest nguồn:
`/home/julius/julius-workspace/ai-labs/projects/system-trading-jev-advisory/state/verification/jev-advisory-20261015T114503Z/protected-before.json`.

SHA-256 manifest: `95a91cf9f9a8afdaa6566ac5bc5b2b994dfea0508bfea4a6bf5701487f071efa`.
Receipt kiểm hiện tại:
`state/verification/jev-review-protected-20261016/boundary-check.json`.

## Trạng thái tại lần kiểm đầu, trước khi được duyệt

Phần này giữ mốc lịch sử trước triển khai. Kết quả sau phê duyệt nằm trong
[báo cáo đợt 1](jev-setup2-review-replay-2026-10-16.md).

- Đã đọc hướng dẫn workspace, các báo cáo Jev, ADR-007 và các điểm nối replay liên quan.
- Đã tạo worktree, kiểm hash; chưa sửa mã, chưa chạy pytest hoặc replay tham chiếu.
- Chưa có dry-run: chưa đếm state, token hoặc chi phí. Không suy ra số lần gọi từ 340 lệnh đóng,
  vì collect phải xét mọi nến `entry=True`, kể cả khi đang có vị thế, rồi khử trùng theo khóa cache.
- Chưa gọi Jev thật, chưa truy cập VPS/Binance hoặc đọc secrets; chưa push/deploy/commit.
- Sau khi duyệt ngoại lệ nguồn, thực hiện đợt 1 tới dry-run và commit local rồi dừng
  tại cổng duyệt gọi Jev thật. Duyệt ngoại lệ nguồn không đồng nghĩa duyệt `--execute`.

Lựa chọn đề xuất: cho ngoại lệ đúng ba file trên. Lựa chọn còn lại: giữ nguyên
99 hash và sửa yêu cầu mục 5 để dùng một replay độc lập; lựa chọn này thay đổi
điểm tích hợp đã chỉ định, nên cũng cần Julius quyết định.
