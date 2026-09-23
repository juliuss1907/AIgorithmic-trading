# Kế hoạch multi-cadence AI paper trading

Ngày cập nhật: 2026-09-23. Trạng thái: **đang triển khai, paper-only**.

## Mục tiêu

Xây hệ thống BTCUSDT paper trading có hai scope Spot Donchian và Perp intraday. Risk engine luôn
deterministic; Jev ra quyết định theo lịch; LLM phân tích evidence và đề xuất rule nhưng không có quyền
thay đổi hard-risk hoặc tự kích hoạt rule.

## Hợp đồng đã khóa

- Binance BTCUSDT là venue paper chính; Hyperliquid chỉ cung cấp shadow evidence.
- Perp dùng isolated 3x. Không có endpoint đặt lệnh thật hoặc ví on-chain trong v1.
- Risk/mark 5 giây; order book 15 giây; Perp Jev numeric 30 giây; derivatives 60 giây.
- Compact Jev shadow 15 phút trên cùng snapshot với numeric primary.
- Spot chỉ gọi Jev khi có nến ngày đã đóng mới và Donchian setup đủ điều kiện.
- News ingest 30 phút; LLM thesis 60 phút khi evidence đổi; retrospective 09:00 Việt Nam.
- Rule proposal qua replay và 72 giờ decision-only soak; kích hoạt thủ công.
- Mọi signal, trade, model call, outcome và evaluation được lưu append-only trong SQLite.

## Các phase

1. Chốt tài liệu và trang System Plan.
2. Tách scheduler đa nhịp, market cache và deterministic risk loop.
3. Thêm compact-state shadow A/B với contract variant/mode/pair.
4. Tính forward outcomes từ snapshot đã lưu.
5. Đánh giá A/B và tạo retrospective deterministic lúc 09:00.
6. Tách hourly thesis khỏi daily rule proposal; thêm rule lifecycle thủ công.
7. Bổ sung dashboard vận hành, doctor và chạy paper soak.

## Điều kiện hoàn thành

- Risk loop tiếp tục hoạt động khi AI hoặc nguồn phụ lỗi.
- Không AI job nào chạy trùng sau restart.
- Shadow decision không thể tạo fill hoặc notification.
- Migration bảo toàn journal cũ và append-only triggers.
- Rule/compact state không tự động promotion.
- Full test suite pass và hệ thống chạy paper ổn định tối thiểu 72 giờ.

## Git policy

Mỗi phase có một local commit độc lập. Không push cho tới khi chủ dự án xem kết quả và yêu cầu rõ ràng.
