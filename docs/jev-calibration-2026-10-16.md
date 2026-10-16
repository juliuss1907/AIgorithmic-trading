# Hiệu chỉnh của Jev và cỡ mẫu tín hiệu Setup-2 lịch sử (2026-10-16)

Trạng thái: **ghi nhận, chỉ đọc.** Truy vấn database VPS (`mode=ro`, container `worker`) và file kết quả G4 trên VPS; không sửa gì.
Mục đích: làm mốc trước khi đổi cách hỏi Jev (nhánh A trong cấu trúc đề xuất) và ước lượng mẫu cho replay lịch sử.

## 1. Cỡ mẫu tín hiệu lịch sử (replay gate G4)

Nguồn: `~/.local/state/aigorithmic-trading/adr007/gates.txt` trên VPS. Replay từng route riêng lẻ (không phải danh mục slot), cửa sổ
từ khoảng 2024-04 (ETH Spot: 2024-04-10 → 2026-10-14) đến 2026-10-14.

| Route | Lệnh đóng | Lãi ròng | DD |
|---|---|---|---|
| ETH Perp | 46 | +5,44% | 5,53% |
| ETH Spot | 36 | +1,98% | 5,26% |
| NEAR Perp | 39 | +5,03% | 4,93% |
| NEAR Spot | 40 | +9,38% | 4,94% |
| SOL Perp | 42 | +4,54% | 3,71% |
| SOL Spot | 39 | +1,53% | 5,92% |
| ZEC Perp | 31 | −1,69% | 5,73% |
| ZEC Spot | 47 | +15,09% | 4,54% |
| HYPE Perp | 20 | −4,26% | 7,11% |
| HYPE Spot | 0 | 0 | 0 |
| **Tổng** | **340** | | |

Khoảng 340 lệnh trong khoảng 30 tháng, tức chừng 2–3 lệnh mỗi tuần trên 10 route. Đủ để thấy một bộ lọc có tác dụng lớn (ví dụ bỏ được
phần lớn lệnh lỗ nặng), không đủ để chứng minh một cải thiện nhỏ. Nếu chọn ngưỡng trên 2024–2025 và kiểm trên 2026, phần kiểm còn khoảng
một phần ba số lệnh (ước theo thời gian, chưa đếm theo ngày lệnh).

## 2. Hiệu chỉnh của Jev với cách hỏi hiện tại

Dữ liệu: 115.164 câu trả lời `perp_intraday` (2026-09-25 → 2026-10-15; BTC, ETH, SOL, NEAR, ZEC, HYPE), câu hỏi "Choose the appropriate
… perpetual trading action now" trên snapshot số 1h. Câu trả lời đến mỗi 30 giây nên gần như trùng nhau; giữ **1 câu mỗi coin mỗi 15 phút**,
còn **3.880 mẫu**. Lợi suất sau 1h lấy từ `signal_outcomes`; sau 4h và 24h tính từ nến 15m Perp trong `setup2_candles`.
`p_up` = P(Buy + Strong Buy) / P(Buy + Strong Buy + Sell + Strong Sell).

**Brier score (thấp hơn là tốt hơn):**

| Khung | n | Jev | Đoán theo tỉ lệ nền |
|---|---|---|---|
| 1h | 3.876 | 0,298 | 0,250 |
| 4h | 2.910 | 0,283 | 0,250 |
| 24h | 2.907 | 0,295 | 0,242 |

Ở cả ba khung, xác suất hướng của Jev **kém hơn đoán theo tỉ lệ nền**. Jev quá tự tin: khi nó nói P(up) ≈ 0,85 thì giá lên 54% (1h),
58% (4h), 47% (24h); khi nó nói ≈ 0,05 thì giá lên 43%, 29%, 15%. Hai đầu cực có chút thông tin xếp hạng, phần giữa không đơn điệu.

**Theo vùng RSI (chỗ tín hiệu Setup-2 xuất hiện):**

| Vùng | Jev chọn | n | Lợi suất TB 4h | Tỉ lệ lên 4h | Lợi suất TB 24h | Tỉ lệ lên 24h |
|---|---|---|---|---|---|---|
| RSI < 30 | phía mua | 306 | +0,55% | 70% | −0,19% | 57% |
| RSI > 70 | phía bán | 73 | −0,92% | 18% | −2,33% | 18% |

Khi quá bán, nhịp hồi của Jev **đúng trong 1–4 giờ**, nhưng tới 24h thì mất. Lệnh Setup-2 giữ nhiều ngày. Jev đang trả lời đúng câu
"intraday" mà nó được hỏi, và đó là khung khác với khung của luật. Đây là thêm một lý do để hỏi Jev đúng câu, đúng khung thời gian.

## Giới hạn

- Chỉ 20 ngày, một chế độ thị trường (tỉ lệ lên 24h chỉ 41%, mọi nhóm đều âm sau 24h). Không suy rộng.
- Mẫu 15 phút liền nhau vẫn tương quan; số mẫu độc lập thực tế nhỏ hơn 3.880.
- Đây là đo cho câu hỏi hiện tại, không nói gì về câu hỏi mới có bối cảnh Setup-2.
