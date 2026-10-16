# Jev Setup-2 review v2: collect và đánh giá

Đợt tiếp theo sau [kiểm chứng đợt 1](jev-setup2-review-replay-2026-10-16.md).
Julius đã duyệt gọi thật với trần 1 USD và đánh giá theo mục 7 của note gốc.
Không merge, push, deploy hay thay đổi runtime/soak.

## Câu hỏi v2 và dry-run trước gọi thật

Chỉ bỏ câu `RSI extremes alone do not veto a multi-day trend.` khỏi phần dẫn
chung; không thêm câu dẫn nào. `QUESTION_SET_VERSION=setup2-review-questions-v2`.
Test khóa hash toàn bộ ba câu hỏi sau thay đổi:
`ee1b7367ad9228d4f1accd94e75515052468354b0da0a473b40e6125468fab60`.

Dry-run v2: **796 entry bars, 379 state, 379 khóa thiếu, 0 network calls**.
Ước tính 0,012571272 USD; dự toán byte + overhead với hai retry 0,199685052 USD.
Đây là dự toán, không phải chi phí đo thực tế.

- Receipt: `~/.local/state/aigorithmic-trading/jev-review/collect-dry-run-20261016-v2.json`
- SHA256 file: `0b73a473aa82fd0fc68e8aab932fd9aae160d0c774fd548bd8fc19cd6a863683`
- `plan_hash`: `18c49bd7cdea2f78eb080ea18eb20cd21cf146575e7f61b346771f0de4939e7a`
- `request_set_hash`: `7f7d654a4b6c265bcc089af2e80dfdbae78051ab97b7fdfffbdc2ae4b65d854b`

Trước collect, thêm sổ đặt chỗ chi phí append-only trong cache riêng. Mọi lần
gọi, retry, resume đều tính vào trần 1 USD; khi ngắt không mất khoản đã đặt chỗ.
Nguồn giá đã đọc lại ngày 16/10:
[OpenRouter endpoint metadata](https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints)
và [TypeSafe models](https://docs.typesafe.ai/models): 0,042 USD/triệu input token,
output miễn phí. Đặt chỗ theo context tối đa 262.144 token mỗi call, chỉ giải phóng
phần dư khi có usage hợp lệ. Thiếu usage giữ nguyên khoản đặt chỗ.

[Giao thức đánh giá](jev-setup2-review-evaluation-protocol-2026-10-16.md) chốt
16 tổ hợp, tiêu chí chọn/tie-break, chia kỳ, bootstrap và calibration trước khi
có câu trả lời thật. Cấu hình thắng tuning sẽ được ghi và khóa trước test.

Kiểm chứng trước collect: 24 test encoder/cache/plan và 18 test provider/CLI/budget
qua. Chưa sửa thêm bất kỳ file nào trong 99 file source được bảo vệ ở bước này.

Phần kết quả collect và đánh giá được bổ sung sau khi hoàn tất, không thay thế
receipt, manifest hay cache fixture của đợt 1.
