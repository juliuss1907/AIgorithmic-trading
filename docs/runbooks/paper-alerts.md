# Runbook: BTC paper Telegram alerts

## Điều đó có nghĩa gì

- Không có summary sau 09:20 Việt Nam: timer chưa chạy, worker còn retry hoặc Telegram delivery lỗi.
- Cảnh báo `KHÔNG TẢI ĐƯỢC DỮ LIỆU`: Binance public snapshot thất bại; systemd thử lại mỗi 10 phút.
- Cảnh báo `BTC PAPER ĐÃ DỪNG`: risk hoặc reconciliation guard đã halt account; cần kiểm tra ngay.

Telegram là fail-open. Việc không nhận tin không chứng minh cycle thất bại; nguồn sự thật là SQLite,
JSONL audit log và systemd journal.

## Kiểm tra đầu tiên

```bash
make paper-timer-status
systemctl --user status system-trading-paper.service --no-pager
journalctl --user -u system-trading-paper.service --since today --no-pager
uv run --frozen python -m lab paper-review --account e573cbe236d74c8d83912c602cb041fb
```

Nếu cycle thành công nhưng không có tin, kiểm tra file config tồn tại với mode `600`, sau đó chạy
`make paper-alert-test`. Không in nội dung file hoặc token ra terminal/log.

## Xử lý

1. Fetch failure: chờ retry; khi data phục hồi, xác nhận incident chuyển `recovered` rồi thêm operator
   note bằng `paper-ack-incident`.
2. Telegram failure: sửa token/chat ID hoặc kết nối; outbox `failed` được retry ở lần worker kế tiếp.
3. Account halt: không tự sửa SQLite hay tự khởi động lại. Đối chiếu `halt_reason`, cycle, fill và ledger.
4. Nếu chưa rõ nguyên nhân, giữ account halted và giữ nguyên `state/`, `data/`, `runs/` để điều tra.

Operator chịu trách nhiệm xử lý; hệ thống này chưa có broker thật hoặc on-call bên ngoài.
