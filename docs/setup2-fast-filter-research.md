# Setup-2: bộ lọc nhanh hơn và chia size theo bộ lọc (thiết kế nghiên cứu)

Trạng thái: **thiết kế, viết và commit trước khi chạy.** Kết quả sẽ được ghi ở mục cuối.

Đây là nghiên cứu cục bộ trên dữ liệu đã thấy. Nến và quan sát Setup-2 được đọc từ database VPS ở chế độ `mode=ro`. Nghiên cứu không đụng tới runtime, active set, ADR-007, soak hay Jev, và không gọi model.

## Vì sao làm

Ngày 19/10 Julius thấy Setup-2 theo xu hướng khá chậm, và đề xuất 5 hướng:
1. dùng bộ lọc nhanh hơn: EMA 20/50 thay cho EMA 50/200, hoặc ADX dốc lên so với 3–5 nến trước;
2. breakout luôn vào, nhưng chỉ 0,5x khi bộ lọc chưa xác nhận;
3. vào hai tầng: nửa vị thế lúc breakout, nửa còn lại khi EMA/ADX xác nhận;
4. xác nhận breakout bằng volume, thân nến lớn hơn ATR, hoặc độ dốc EMA;
5. vào lại khi giá hồi về EMA.

Đối chiếu với code (`replay_v2/donchian_filters.py::entry_filters`):
- Setup-2 không chờ EMA cắt nhau. Luật chỉ xét giá nằm phía nào của EMA200, giá nằm phía nào của EMA50, và EMA50 có dốc theo chiều lệnh không.
- ADX đã phải "dốc lên" (cao hơn nến trước) và trên 20.
- Volume > 1,2 × MA20 đã là một bộ lọc.
- Hướng 5 đã được thử: Setup-3 (bán khi giá hồi về EMA200) không đạt, và chờ 1 nến xác nhận làm kết quả tệ hơn. Hướng 5 không được thử lại ở đây.
- EMA 20/50 đã thua trên H1 (`H1-EMA50-20` trong `docs/setup2-h1-research.md`), nhưng chưa thử trên H4.

Nghiên cứu này thử những phần chưa được kiểm: hướng 1 trên H4, hướng 2, hướng 3, và "thân nến > ATR" của hướng 4.

## Câu hỏi

Trên danh mục slot của ADR-007 (5 coin, Spot long + Perp short, tối đa 3 vị thế), có biến thể nào bắt xu hướng sớm hơn mà **tốt hơn luật hiện tại** về lợi nhuận, về lợi nhuận trên mức sụt giảm, và ổn định ở cả hai nửa dữ liệu không?

## Dữ liệu

- **Coin:** ETH, HYPE, NEAR, SOL, ZEC; Spot chỉ long, Perp chỉ short.
- **Nguồn:** bảng `setup2_candles` trên VPS. Quan sát được tính bằng chính `setup2.series_observations` (hồ sơ `setup2_slots_v1`), nên breakout, bộ lọc, blocker, cờ thoát Donchian và hệ số size đều đúng như runtime đang tính.
- **Thêm cho các biến thể:** EMA20, EMA20 nến trước và EMA50 từ `indicator_series(ema_fast=20, ema_slow=50)`; ADX của 3 nến trước; giá mở nến breakout. Thêm nến M15 Perp của 3 nến H4 sau mỗi nến breakout Perp, để mô phỏng lệnh limit.
- **Giai đoạn:** toàn bộ từ khi đủ số nến khởi động tới nến H4 cuối lúc lấy dữ liệu, chia làm **hai nửa bằng nhau theo thời gian**.

## Mô phỏng danh mục (giống phép kiểm danh mục slot ngày 19/10)

- Chọn lệnh bằng chính `select_entries` và `pnl_gate_blocks` của runtime: tối đa 3 vị thế, mỗi coin một vị thế, xếp hạng theo ADX, đổi vị thế khi chênh ADX > 5, và cổng lãi lỗ.
- Size: 50% vốn mỗi sleeve × 1/3 × hệ số ATR (`min(1, 2% / ATR%)`).
- Vào lệnh ở giá mở nến kế tiếp. Stop ATR14 × 3 kéo theo giá, tối đa 5% từ giá vào. Thoát Donchian 10 ở giá mở nến kế tiếp. Giá mở nhảy qua stop thì thoát ở giá mở.
- Chi phí cả vòng: Perp 0,20%, Spot 0,30%. Không tính funding, không tính luật lỗ 3% mỗi ngày.
- Lợi nhuận là tổng đơn giản theo % vốn; mức sụt giảm đo trên đường vốn theo lúc đóng lệnh.

## Các biến thể (cố định từ bây giờ)

Mọi biến thể vẫn bắt buộc có breakout Donchian 25 và không có blocker (thiếu volume profile, khối lượng Spot còn dùng giá Perp thay, stop ATR không hợp lệ). Chỉ phần bộ lọc thay đổi.

| Mã | Theo hướng | Luật vào lệnh |
|---|---|---|
| B | mốc | Luật hiện tại: đủ cả EMA200, EMA50, ADX, DMI, volume, volume profile |
| V1 | 2 | Breakout luôn vào. Đủ bộ lọc thì size 1x; thiếu bất kỳ bộ lọc nào thì 0,5x |
| V2 | 3 | Đủ bộ lọc lúc breakout thì vào 1x như B. Nếu thiếu: vào 0,5x lúc breakout; trong 6 nến H4 tiếp theo (24 giờ), ở nến đầu tiên mà EMA200, EMA50, ADX và DMI cùng đạt thì thêm 0,5x ở giá mở nến kế tiếp, nếu vị thế còn mở. Hai phần dùng chung stop và thoát cùng lúc |
| V3 | 1 | Thay EMA50/EMA200 bằng EMA20/EMA50: giá đúng phía EMA50, giá đúng phía EMA20 và EMA20 dốc theo chiều lệnh. Các bộ lọc khác giữ nguyên |
| V4 | 1 | Thay điều kiện ADX (> 20 và cao hơn nến trước) bằng: ADX cao hơn ADX của 3 nến trước, không có ngưỡng. DMI giữ nguyên |
| V5 | 4 | Thay bộ lọc EMA50 và ADX/DMI bằng: thân nến breakout theo chiều lệnh ≥ 1 ATR, tức `chiều × (đóng − mở) ≥ ATR`. Giữ EMA200, volume, volume profile |

Ở V1 và V2, hệ số size 0,5 nhân với hệ số ATR. Lệnh 0,5x vẫn chiếm một trong 3 slot.

Mỗi biến thể chạy với **hai cách vào lệnh**:
- (a) lệnh thị trường ở giá mở nến kế tiếp, như runtime hiện nay;
- (b) Perp vào bằng limit 0,25 ATR trên giá đóng nến tín hiệu, chờ 1 nến, đọc khớp lệnh trên M15; Spot vẫn vào bằng lệnh thị trường. Đây là cách vào đang được theo dõi shadow.

Ở V2, phần thêm 0,5x luôn vào bằng lệnh thị trường.

## Tiêu chí đạt (cố định trước khi chạy)

Một biến thể **đạt** nếu đủ cả 5 điều sau:

1. Lợi nhuận tổng cao hơn B ở **cả hai** cách vào lệnh, tức cao hơn cả hai mốc (B-a và B-b).
2. Mức sụt giảm tối đa ≤ 10% ở cả hai cách vào lệnh.
3. Lợi nhuận chia mức sụt giảm không thấp hơn B, ở cả hai cách vào lệnh. Điều này chặn trường hợp biến thể lãi hơn chỉ vì vào nhiều vốn hơn (V1, V2).
4. Ở cách vào (a), lợi nhuận cao hơn B trong **từng** nửa dữ liệu.
5. Ở cách vào (a) với chi phí gấp đôi, lợi nhuận vẫn cao hơn B cũng với chi phí gấp đôi.

**Nếu có biến thể đạt:** đề xuất theo dõi shadow trên soak, giống limit 0,25 ATR, trong một kế hoạch riêng cần Julius duyệt. Nghiên cứu không đổi luật đang chạy. Thử 5 biến thể cùng lúc thì khả năng một biến thể đạt nhờ may mắn không nhỏ, nên đạt ở đây chưa đủ để viết ADR.

**Nếu không có biến thể nào đạt:** ghi kết quả vào tài liệu này và dừng. Không dò thêm ngưỡng.

## Đo gì

Với mỗi biến thể và mỗi cách vào lệnh:
- lợi nhuận tổng, mức sụt giảm, lợi nhuận/sụt giảm;
- phần của Perp và Spot; số lệnh; lợi nhuận trung bình mỗi lệnh;
- lợi nhuận theo năm và theo từng nửa dữ liệu;
- số tín hiệu bị chặn vì hết slot, vì chênh ADX chưa đủ, hay vì cổng lãi lỗ;
- riêng các lệnh mà B không có (lệnh "thêm"): số lệnh, lợi nhuận trung bình, tỉ lệ thắng;
- riêng V2: bao nhiêu lệnh được thêm tầng 2, và kết quả của phần tầng 2.

## Dự đoán (viết trước khi chạy)

- **V1 không đạt.** Nghiên cứu 703 ngày cho thấy khoảng 185 lệnh bị bộ lọc loại nhìn chung là lỗ. Vào chúng với 0,5x vẫn là thêm lệnh có kỳ vọng âm, làm mức sụt giảm tăng. Thêm vào đó, các lệnh này chiếm slot của lệnh tốt.
- **V2 không đạt**, nhưng tốt hơn V1: phần tầng 2 có lãi, còn phần tầng 1 lỗ như V1.
- **V3 không đạt:** nhiều lệnh hơn, ngắn hơn, giống kết quả trên H1.
- **V4 gần với B**, chênh ít, không đạt tiêu chí 4.
- **V5 có ít lệnh thêm** vì nến có thân ≥ 1 ATR vượt kênh không nhiều. Không chắc kết quả.

## Giới hạn

- Dữ liệu đã thấy, 5 coin được chọn sau khi biết kết quả, mẫu khoảng 2,5 năm, và không có phần kiểm riêng. Tiêu chí 4 (cả hai nửa) chỉ thay thế một phần cho phần kiểm riêng.
- Không có funding, không có Jev, không có luật lỗ 3% mỗi ngày.
- HYPE Spot dùng giá Perp cho đoạn chưa có dữ liệu Spot; runtime chặn lệnh khi cửa sổ volume chạm đoạn này, và mô phỏng giữ đúng blocker đó.
- Ở V2, phần tầng 2 có thể lỗ quá 5% nếu stop kéo theo giá vẫn còn xa giá vào tầng 2.
