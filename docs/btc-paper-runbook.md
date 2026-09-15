# Runbook BTCUSDT research và paper bot

## Trạng thái hiện tại

Snapshot learning: `c225bc7732a0cc1d347adc7925a923b41fb3b12150db1b0ac137b9cf0cf775a2`,
3.059 nến từ 2017-08-17 đến 2025-12-31. Promotion gate đã khóa `stay_cash`.
Không mở holdout, không tạo paper account và không có giao dịch thật.

## Khởi động

```bash
make web
# Nếu port 8000 đang bận: make web PORT=8765
```

Mở `http://127.0.0.1:8000/paper`. Dashboard hiện gate, tài khoản paper, fill, ledger và halt.
API tương ứng bắt đầu tại `/api/promotion`, `/api/paper/accounts` và `/api/ai/status`.

Chỉ chạy worker khi đã có account hợp lệ sau gate + holdout:

```bash
make paper-worker
# hoặc chạy đúng một chu kỳ
uv run --frozen python -m lab.paper_worker --once --account ACCOUNT_ID
```

Worker thức lúc 00:02 UTC, tải nến đã đóng, lưu snapshot, lấy public bid/ask rồi mô phỏng fill.
Không cần và không đọc Binance API key.

## Tái lập nghiên cứu

```bash
uv run --frozen python -m lab fetch --config experiments/btc-sma-learning.json
uv run --frozen python -m lab run --config experiments/btc-sma-learning.json --output runs/btc-sma-learning-new
uv run --frozen python -m lab run --config experiments/btc-rsi-bollinger-learning.json --output runs/btc-rsi-bollinger-learning-new
uv run --frozen python -m lab run --config experiments/btc-donchian-learning.json --output runs/btc-donchian-learning-new
```

Không chạy lại lệnh `gate` lên state hiện tại: latch sẽ từ chối ghi đè. Muốn thử một giả thuyết mới,
dùng database promotion mới và ghi rõ 2018–2025 đã được quan sát.

## Chẩn đoán

- Chu kỳ nào vừa chạy? Xem `paper_cycles` qua dashboard/API hoặc `state/paper-logs/{account}.jsonl`.
- Vì sao không có lệnh? Xem signal, account `status/halt_reason`, exchange rules và intent list.
- Đối soát có qua? `reconciliation_ok` nằm trên mỗi cycle; ledger API cho cash/BTC delta.
- Worker lỗi tải: account không được tạo fill; sửa kết nối/dữ liệu rồi kiểm tra halt trước khi chạy lại.
- Rule sàn thay đổi hoặc ledger lệch: bot tự halt. Không tự sửa SQLite để chạy tiếp.

## Sao lưu và phục hồi

Sao lưu cùng lúc `data/`, `runs/` và `state/` khi worker đã dừng. Dataset/run là bằng chứng bất biến;
SQLite chứa gate, paper account, ledger, halt và ghi chú. Không xóa một phần rồi tiếp tục cùng account.

## Nguồn dữ liệu

Client chỉ gọi public allowlisted host `https://data-api.binance.vision` cho klines,
exchange info và book ticker. Không có code ký request hoặc endpoint `/order`.
