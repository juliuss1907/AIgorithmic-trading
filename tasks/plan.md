# Kế hoạch BTC-first: bot thuật toán + AI copilot

Ngày chốt: 2026-09-15. Trạng thái: **đã triển khai MVP nghiên cứu và paper infrastructure**.

## Mục tiêu

Xây bot BTCUSDT spot chạy bằng quy tắc có thể audit. Thuật toán là bên duy nhất tạo signal/order intent;
AI chỉ nhận evidence packet bất biến và trả văn bản giải thích. Không có endpoint đặt lệnh thật.

## Hợp đồng đã khóa

- Binance BTCUSDT spot, nến `1d`, lịch UTC 24/7; chỉ dùng nến đã đóng.
- Vốn paper 10.000 USDT; long/cash, không đòn bẩy; target tối đa 50%.
- Taker fee 10 bps mỗi fill; stress slippage 0/5/10 bps, mức chính 5 bps.
- Tự dừng khi drawdown tài khoản đạt 20%, đối soát lỗi, dữ liệu lỗi hoặc quy tắc sàn thay đổi.
- Ba ứng viên: SMA 20/50; RSI14 + Bollinger20/2; Donchian20/10 + ATR14.
- Learning/validation là tám fold năm 2018–2025. Holdout là 2026-01-01 → 2026-08-31,
  chỉ mở một lần sau khi promotion gate đã khóa.

## Promotion gate

Ứng viên phải có lãi ít nhất 5/8 fold ở base cost, lợi nhuận gộp dương ở stress 10 bps,
và drawdown tệ nhất không quá 20%. Nếu nhiều ứng viên qua: drawdown thấp hơn → median return cao hơn
→ turnover thấp hơn. Không ai qua thì giữ cash.

Kết quả dữ liệu thật đã khóa ngày 2026-09-15: cả ba ứng viên đều trượt điều kiện drawdown.
Quyết định hiện tại là `stay_cash`; holdout chưa mở và paper account không được tạo.

## Thành phần đã triển khai

1. Snapshot Binance v2 có market/venue/calendar, exchange rules và checksum; catalog cũ vẫn tương thích.
2. Ba signal engine có indicator evidence và test không nhìn tương lai.
3. Crypto execution adapter có quantity step, min-notional, fee, slippage, next-open và CAGR 365.
4. Walk-forward gate + SQLite latch ngăn ghi đè quyết định và mở holdout lần hai.
5. Paper broker SQLite có cycle → signal → intent → fill → ledger → reconciliation và kill switch.
6. Worker 00:02 UTC chỉ dùng public market-data API; không có secret hay live-order transport.
7. AI provider interface read-only; khi chưa có provider, API/UI hiện `disabled`.
8. Dashboard `/paper` và API audit cho account/cycle/signal/intent/fill/ledger.

## Việc tiếp theo hợp lệ

Không nới gate sau khi xem kết quả. Bước tiếp theo là viết giả thuyết rủi ro mới trước khi chạy,
ví dụ sizing theo volatility hoặc stop/risk budget có lý do kinh tế. Mọi biến thể phải dùng run mới và
2018–2025 là dữ liệu đã quan sát. Chỉ khi một rule mới qua gate mới tạo config holdout, mở holdout đúng một lần,
rồi shadow-paper tối thiểu tám tuần trước khi thảo luận broker thật.
