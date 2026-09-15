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
- [x] Worker public-data-only lúc 00:02 UTC.
- [x] EvidencePacket và AIProvider read-only; provider mặc định disabled.
- [x] Dashboard/API paper; kiểm tra Chromium desktop/mobile, accessibility tree và console.
- [x] Full automated suite và runbook.

## Chưa được phép vì gate hiện tại không qua

- [ ] Mở holdout 2026.
- [ ] Tạo tài khoản paper cho chiến lược được promote.
- [ ] Chạy shadow-paper tám tuần.
- [ ] Chọn broker hoặc thêm endpoint giao dịch thật.

## Backlog có điều kiện

- [ ] Viết trước một giả thuyết kiểm soát drawdown mới; không tune để khớp riêng các năm đã xem.
- [ ] Thêm sizing volatility/ATR như một experiment family có typed contract và golden tests.
- [ ] Chạy lại cùng gate trên run mới; giữ toàn bộ thất bại làm bằng chứng.
- [ ] Chỉ sau khi holdout qua: tạo paper account từ snapshot/strategy đã khóa.
- [ ] Chọn AI provider/model/ngân sách; thêm adapter thật mà không thay quyền hạn read-only.
