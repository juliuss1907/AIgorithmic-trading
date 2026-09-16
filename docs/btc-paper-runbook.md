# Runbook BTCUSDT research và paper bot

## Trạng thái hiện tại

Snapshot learning: `c225bc7732a0cc1d347adc7925a923b41fb3b12150db1b0ac137b9cf0cf775a2`,
3.059 nến từ 2017-08-17 đến 2025-12-31. Gate baseline vẫn khóa `stay_cash`; gate entry-volatility v1
đã khóa chọn Donchian với worst drawdown −18,06%. Candidate contract đã khóa run
`e1bb13fe60817ce83d85`, strategy, entry-volatility sizing, chi phí và checksum. Holdout chưa mở,
chưa tạo paper account và không có giao dịch thật.

## Khởi động

```bash
make web
# Nếu port 8000 đang bận: make web PORT=8765

# Xem gate entry-volatility v1 thay vì gate baseline:
LAB_PROMOTION_STATE=state/btc-promotion-entry-vol20-v1.sqlite3 make web
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

Binance hiện là nguồn dữ liệu và bộ quy tắc thị trường spot. Paper broker chạy nội bộ; chưa chọn nơi
đặt lệnh thật và code không có authenticated client hay endpoint `/order`.

## Tái lập nghiên cứu

```bash
uv run --frozen python -m lab fetch --config experiments/btc-sma-learning.json
uv run --frozen python -m lab run --config experiments/btc-sma-learning.json --output runs/btc-sma-learning-new
uv run --frozen python -m lab run --config experiments/btc-rsi-bollinger-learning.json --output runs/btc-rsi-bollinger-learning-new
uv run --frozen python -m lab run --config experiments/btc-donchian-learning.json --output runs/btc-donchian-learning-new

uv run --frozen python -m lab run --config experiments/btc-sma-entry-vol20-v1.json --output runs/btc-sma-entry-vol20-v1-new
uv run --frozen python -m lab run --config experiments/btc-rsi-bollinger-entry-vol20-v1.json --output runs/btc-rsi-bollinger-entry-vol20-v1-new
uv run --frozen python -m lab run --config experiments/btc-donchian-entry-vol20-v1.json --output runs/btc-donchian-entry-vol20-v1-new
```

Không chạy lại lệnh `gate` lên bất kỳ state đã khóa nào: latch sẽ từ chối ghi đè. Gate volatility v1
nằm tại `state/btc-promotion-entry-vol20-v1.sqlite3`; gate baseline vẫn nằm tại
`state/btc-promotion.sqlite3`. Muốn thử giả thuyết khác, dùng database mới và ghi rõ 2018–2025 đã được quan sát.

## Holdout đã chuẩn bị nhưng chưa chạy

Config `experiments/btc-donchian-holdout-v1.json` và lệnh `holdout` đã được đăng ký trước. Lệnh sẽ tự
tải snapshot đến 2026-08-31, so byte lịch sử 2017–2025 với snapshot learning, chạy/import artifact,
kiểm tra lineage/checksum rồi mới latch metrics. Không truyền strategy hoặc metrics thủ công.

Không chạy lệnh dưới đây trong giai đoạn hardening hiện tại; đây là thao tác mở holdout một lần cần
review riêng:

```bash
uv run --frozen python -m lab holdout \
  --state state/btc-promotion-entry-vol20-v1.sqlite3
```

Nếu tiến trình dừng sau khi artifact đã hoàn chỉnh nhưng trước latch, chạy lại cùng lệnh sẽ verify và
tiếp tục. Artifact thiếu, bị đổi, sai parent/config hoặc lịch sử Binance bị revise đều làm lệnh dừng.

## Chẩn đoán

- Chu kỳ nào vừa chạy? Xem `paper_cycles` qua dashboard/API hoặc `state/paper-logs/{account}.jsonl`.
- Vì sao không có lệnh? Xem signal, `entry_armed`, account `status/halt_reason`, exchange rules và intent list.
- Đối soát có qua? `reconciliation_ok` nằm trên mỗi cycle; ledger API cho cash/BTC delta.
- Worker lỗi tải: account không được tạo fill; sửa kết nối/dữ liệu rồi kiểm tra halt trước khi chạy lại.
- Rule sàn thay đổi hoặc ledger lệch: bot tự halt. Không tự sửa SQLite để chạy tiếp.

## Sao lưu và phục hồi

Sao lưu cùng lúc `data/`, `runs/` và `state/` khi worker đã dừng. Dataset/run là bằng chứng bất biến;
SQLite chứa gate, paper account, ledger, halt và ghi chú. Không xóa một phần rồi tiếp tục cùng account.

## Nguồn dữ liệu

Client chỉ gọi public allowlisted host `https://data-api.binance.vision` cho klines,
exchange info và book ticker. Không có code ký request hoặc endpoint `/order`.
