# Checklist BTC-first

## Đã hoàn thành

- [x] Contract BTCUSDT spot/1d/UTC 24/7 và risk policy 50%/20%.
- [x] Binance public data client, closed-candle guard, pagination, retry, exchange rules và snapshot v2.
- [x] SMA, RSI/Bollinger và Donchian/ATR với indicator evidence + causal tests.
- [x] Crypto execution adapter: next-open, step size, min-notional, 10 bps fee, 0/5/10 bps slippage.
- [x] Benchmark 50%-allocated buy-and-hold và reference 100%.
- [x] Tám fold 2018–2025, promotion scoring và latch SQLite trước holdout.
- [x] Chạy dữ liệu thật cho ba ứng viên; gate khóa `stay_cash` do cả ba vượt drawdown 20%.
- [x] Paper ledger, idempotent cycle, reconciliation, drawdown halt, rule-change halt và kill switch.
- [x] Worker public-data-only lúc 09:00 Việt Nam bằng systemd user timer.
- [x] EvidencePacket và AIProvider read-only; provider mặc định disabled.
- [x] Dashboard/API paper; kiểm tra Chromium desktop/mobile, accessibility tree và console.
- [x] Full automated suite và runbook.
- [x] Đăng ký trước giả thuyết entry-volatility 20 ngày/20% năm trên cả ba strategy.
- [x] Thêm typed sizing contract, causal/golden tests và replay fixed-sizing không đổi.
- [x] Chạy ba run mới; gate versioned chọn Donchian với worst drawdown −18,06%.
- [x] Khóa candidate Donchian vào run/dataset/config/checksum đã verify.
- [x] Harden pipeline holdout one-shot; chưa tải hoặc chạy dữ liệu 2026.
- [x] Persist volatility sizing vào paper account và thêm chính sách chờ entry mới.
- [x] Mở holdout 2026 một lần; Donchian qua với +7,67% return và −7,98% max drawdown.

## Đang chạy shadow paper

- [x] Tạo tài khoản paper idempotent từ candidate/holdout contract đã verify.
- [x] Thêm Telegram notifier fail-open và outbox chống gửi trùng.
- [x] Cấu hình Telegram secret ngoài repo và nhận test message thật.
- [ ] Xác nhận scheduler cycle đầu tiên sau 09:00 cùng daily summary.
- [ ] Chạy shadow-paper tám tuần.
- [ ] Chọn broker hoặc thêm endpoint giao dịch thật.

## Backlog có điều kiện

- [ ] Chọn AI provider/model/ngân sách; thêm adapter thật mà không thay quyền hạn read-only.
