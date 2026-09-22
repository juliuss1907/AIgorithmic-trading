# Runbook BTCUSDT research và paper bot

## Trạng thái hiện tại

Snapshot learning: `c225bc7732a0cc1d347adc7925a923b41fb3b12150db1b0ac137b9cf0cf775a2`,
3.059 nến từ 2017-08-17 đến 2025-12-31. Gate baseline vẫn khóa `stay_cash`; gate entry-volatility v1
đã khóa chọn Donchian với worst drawdown −18,06%. Candidate contract đã khóa run
`e1bb13fe60817ce83d85`, strategy, entry-volatility sizing, chi phí và checksum. Holdout 2026 đã qua
với return +7,67% và max drawdown −7,98% ở mức 5 bps. Promoted account
`e573cbe236d74c8d83912c602cb041fb` đã bootstrap nến 2026-09-15, giữ 10.000 USDT cash, 0 BTC và
đang chạy shadow campaign 0/56. Không có giao dịch thật.

## Khởi động

```bash
make web
# Nếu port 8000 đang bận: make web PORT=8765

# Xem gate entry-volatility v1 thay vì gate baseline:
LAB_PROMOTION_STATE=state/btc-promotion-entry-vol20-v1.sqlite3 make web
```

Mở `http://127.0.0.1:8000/paper`. Dashboard hiện gate, tiến độ campaign, incident, fill, ledger và halt.
API tương ứng bắt đầu tại `/api/promotion`, `/api/paper/accounts` và `/api/ai/status`.

Account được tạo bằng lệnh idempotent; `--check` chỉ xác minh contract:

```bash
uv run --frozen python -m lab promote-paper --check
uv run --frozen python -m lab promote-paper
```

User timer đã được cài bằng `make install-paper-timer`. Nó thức lúc 09:00 Việt Nam (02:00 UTC), tải
nến đã đóng, lưu snapshot, lấy public bid/ask rồi mô phỏng fill và thoát. Lỗi tạm thời retry mỗi 10 phút,
tối đa sáu lần. Kiểm tra bằng `make paper-timer-status`; chạy tay một lần bằng:

```bash
uv run --frozen python -m lab.paper_worker --once --account e573cbe236d74c8d83912c602cb041fb
```

Không cần và không đọc Binance API key.

Binance hiện là nguồn dữ liệu và bộ quy tắc thị trường spot. Paper broker chạy nội bộ; chưa chọn nơi
đặt lệnh thật và code không có authenticated client hay endpoint `/order`.

## Telegram alerts

Telegram là lớp quan sát fail-open: cycle và ledger không bị thay đổi khi Telegram lỗi. Summary hằng
ngày gồm signal/fill, equity, cash/BTC, drawdown, reconciliation, incident và tiến độ campaign. Fetch
failure hoặc account halt tạo cảnh báo ngay; outbox SQLite chống gửi trùng khi systemd retry.

Tạo bot bằng BotFather, nhắn `/start` cho bot và lấy numeric chat ID qua phương thức `getUpdates` trong
tài liệu Telegram Bot API. Sau đó tạo file chỉ owner đọc được, **không đặt file này trong repository**:

```bash
mkdir -p ~/.config/system-trading
chmod 700 ~/.config/system-trading
$EDITOR ~/.config/system-trading/paper-alerts.env
chmod 600 ~/.config/system-trading/paper-alerts.env
```

Nội dung file:

```text
TELEGRAM_BOT_TOKEN=<token do BotFather cấp>
TELEGRAM_CHAT_ID=<numeric chat id>
```

Cài lại unit để nhận `EnvironmentFile`, gửi tin thử rồi kiểm tra timer:

```bash
make install-paper-timer
make paper-alert-test
make paper-timer-status
```

Không dán token vào issue, commit, log hoặc câu lệnh có thể lưu shell history. Nếu token từng vào Git,
phải revoke và tạo token mới; xóa dòng khỏi commit là chưa đủ.

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

## Holdout đã chạy và khóa kết quả

Pipeline đã mở holdout đúng một lần bằng config `experiments/btc-donchian-holdout-v1.json`:

- Dataset: `e3f4d26e3662d03aa3f4221bec6f8f7b79d56e1a86a4679b7f1774203be9d042`, 3.302 nến đến 2026-08-31.
- Run: `686cad171d0ae4d547ab`.
- Summary SHA-256: `44111a6bd706cd916495ac3550fc8deef0347c6004e861d9060facd3547e76bc`.
- Provenance SHA-256: `fed1447e4d648da9becf9fa14df22b14c63d972b6cf68a0e5ca20d3e11080e29`.
- Base cost 5 bps: return `+7,67%`, max drawdown `−7,98%`, kết quả **qua**.

Raw/adjusted prefix 2017–2025 khớp snapshot learning; 72 learning cases và 216 artifact số liệu cũng
giống byte-for-byte run đã khóa. Latch hiện chỉ trả lại cùng evidence khi retry; không thể thay kết quả.
Promoted account đã được tạo từ contract này; strategy và sizing không thể override.

## Shadow campaign và G6

- Bootstrap không tính vào 56 cycle. Campaign cần đồng thời đủ 56 ngày và 56 scheduler cycle đã đối soát.
- Ngày bỏ lỡ được ghi incident và kéo dài chiến dịch; không backfill bằng giá lịch sử.
- Xem trạng thái: `uv run --frozen python -m lab paper-review --account e573cbe236d74c8d83912c602cb041fb`.
- Ghi chú incident: `uv run --frozen python -m lab paper-ack-incident --account ACCOUNT_ID --incident INCIDENT_ID --note "..."`.
- Chỉ dùng `paper-review --finalize` khi status là `eligible`; kết quả được khóa một lần.
- G6 còn yêu cầu ít nhất một vòng mua–bán hoàn chỉnh. Chưa có thì tiếp tục shadow quá tám tuần.

## Chẩn đoán

- Chu kỳ nào vừa chạy? Xem `paper_cycles` qua dashboard/API hoặc `state/paper-logs/{account}.jsonl`.
- Vì sao không có lệnh? Xem signal, `entry_armed`, account `status/halt_reason`, exchange rules và intent list.
- Đối soát có qua? `reconciliation_ok` nằm trên mỗi cycle; ledger API cho cash/BTC delta.
- Worker lỗi tải: account không được tạo fill; sửa kết nối/dữ liệu rồi kiểm tra halt trước khi chạy lại.
- Timer/log: `systemctl --user status system-trading-paper.timer` và
  `journalctl --user -u system-trading-paper.service`.
- Không nhận Telegram: xem [runbook cảnh báo](runbooks/paper-alerts.md), kiểm quyền file `0600`, rồi
  chạy `make paper-alert-test`.
- Rule sàn thay đổi hoặc ledger lệch: bot tự halt. Không tự sửa SQLite để chạy tiếp.

## Sao lưu và phục hồi

Sao lưu cùng lúc `data/`, `runs/` và `state/` khi worker đã dừng. Dataset/run là bằng chứng bất biến;
SQLite chứa gate, paper account, ledger, halt và ghi chú. Không xóa một phần rồi tiếp tục cùng account.
Backup trước activation nằm tại
`../system-trading-backups/20260916T124954Z-pre-paper-activation/evidence.tar.gz`, SHA-256
`5fde6af356cf9e1a7302da1663294c9b2794ef421dfe0d7987b1a546b331768f`.

## Nguồn dữ liệu

Client chỉ gọi public allowlisted host `https://data-api.binance.vision` cho klines,
exchange info và book ticker. Không có code ký request hoặc endpoint `/order`.
