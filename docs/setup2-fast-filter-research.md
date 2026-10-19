# Setup-2: bộ lọc nhanh hơn và chia size theo bộ lọc (thiết kế nghiên cứu)

Trạng thái: **đã chạy ngày 2026-10-19, không biến thể nào đạt.** Phần thiết kế dưới đây được viết và commit (`517f42b`) trước khi chạy; kết quả ở mục cuối.

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

## Kết quả (2026-10-19)

**Code và dữ liệu:**
- Code: `intraday/replay_v2/fast_filter_study.py`, test ở `tests/test_fast_filter_study.py` (9 test).
- Dữ liệu: xuất bằng `scripts/fast_filter_export.py` lúc 12:04Z, gồm các nến tới nến H4 mở lúc 08:00Z ngày 19/10.
- Hash dữ liệu: `525e8cdd…cf0eedfb`. Hash cấu hình: `4350baa6…2348f9e`.
- Điểm chia hai nửa: 2025-07-15 02:00Z.
- Trước khi chạy các biến thể, mốc B được chạy lại một mình để kiểm tra mô phỏng: lệnh thị trường +39,2% (DD 7,8%), limit +43,3% (DD 5,8%), khớp với phép kiểm danh mục slot (+39,3% và +43,5%; lệch do có thêm vài nến). Sau đó toàn bộ nghiên cứu chạy đúng một lần.

**Kết quả chính** (lợi nhuận và DD tính theo % vốn; "lệnh thêm" là các lệnh B không có):

| | Cách vào | Lợi nhuận | DD | Lợi nhuận/DD | Nửa đầu / nửa sau | Số lệnh | Lệnh thêm: số / TB mỗi lệnh |
|---|---|---:|---:|---:|---|---:|---|
| **B** | thị trường | +39,2% | 7,8% | 5,04 | +13,3 / +25,8 | 320 | – |
| | limit | +43,3% | 5,8% | 7,53 | +13,7 / +29,7 | 304 | – |
| **V1** 0,5x khi thiếu lọc | thị trường | +24,7% | 8,8% | 2,80 | +13,0 / +11,7 | 619 | 463 / +0,50% |
| | limit | +26,9% | 8,1% | 3,33 | +13,6 / +13,3 | 594 | 436 / +0,42% |
| **V2** hai tầng | thị trường | +32,0% | 14,1% | 2,27 | +14,8 / +17,2 | 618 | 462 / +0,49% |
| | limit | +34,1% | 10,5% | 3,23 | +14,2 / +19,9 | 594 | 436 / +0,42% |
| **V3** EMA20/50 | thị trường | +39,9% | 9,2% | 4,35 | +14,9 / +25,0 | 325 | 13 / −1,32% |
| | limit | +42,2% | 6,9% | 6,08 | +14,3 / +28,0 | 309 | 38 / −1,16% |
| **V4** ADX > 3 nến trước | thị trường | **+45,7%** | 8,9% | 5,12 | +20,1 / +25,6 | 349 | 109 / +1,58% |
| | limit | **+51,7%** | 7,1% | 7,32 | +23,5 / +28,2 | 329 | 121 / +1,36% |
| **V5** thân nến ≥ 1 ATR | thị trường | +37,7% | 10,5% | 3,61 | +16,1 / +21,6 | 307 | 115 / +1,67% |
| | limit | +35,4% | 9,1% | 3,89 | +9,5 / +25,8 | 283 | 116 / +1,26% |

Chi phí gấp đôi, lệnh thị trường: B +29,1%, V1 +12,0%, V2 +15,6%, V3 +29,6%, V4 +34,5%, V5 +27,7%.

**Tiêu chí:**

| | 1. Lãi hơn cả hai mốc | 2. DD ≤ 10% | 3. Lãi/DD không thấp hơn | 4. Hơn ở cả hai nửa | 5. Phí gấp đôi | Kết luận |
|---|---|---|---|---|---|---|
| V1 | ✗ | ✓ | ✗ | ✗ | ✗ | **Không đạt** |
| V2 | ✗ | ✗ | ✗ | ✗ | ✗ | **Không đạt** |
| V3 | ✗ | ✓ | ✗ | ✗ | ✓ | **Không đạt** |
| V4 | ✓ | ✓ | ✗ (limit 7,32 < 7,53) | ✗ (nửa sau +25,6 < +25,8) | ✓ | **Không đạt** |
| V5 | ✗ | ✗ | ✗ | ✗ | ✗ | **Không đạt** |

**V1 và V2 thua vì chiếm slot, không phải vì lệnh thêm lỗ.**
- Lệnh thêm có lãi trung bình dương (+0,4% đến +0,5% mỗi lệnh), nhưng thấp hơn nhiều so với lệnh đủ bộ lọc (+1,02%).
- Gấp đôi số lệnh làm 3 slot luôn đầy: số tín hiệu bị chặn tăng từ 53 lên khoảng 300, chủ yếu vì chênh ADX chưa đủ để đổi vị thế (200 so với 43) và vì cổng lãi lỗ (104 so với 10).
- Ở V2, phần tầng 2 được thêm 225 lần, lãi +7,5% vốn (TB +0,51% mỗi lệnh). Phần này không bù được phần slot bị chiếm.

**V4 là biến thể duy nhất đáng chú ý.**
- Lãi hơn B ở cả ba năm, với cả hai cách vào lệnh. Lệnh thị trường: 2024 +11,0 so với +9,4; 2025 +14,2 so với +10,9; 2026 +20,5 so với +18,8.
- 109 lệnh thêm là những breakout có ADX dưới 20 nhưng đang lên trong 3 nến, tức xu hướng mới bắt đầu. Đúng điều Julius muốn bắt. TB +1,58% mỗi lệnh, tỉ lệ thắng 35%.
- V4 trượt hai tiêu chí, cả hai đều rất sát:
  - lợi nhuận/DD với cách vào limit thấp hơn 0,21;
  - nửa sau thấp hơn 0,2 điểm, vì nửa sau năm 2025 kém hơn B.
- Theo đúng thiết kế, V4 **không đạt** và không được dò thêm.

**Chấm các dự đoán:**

| Dự đoán | Kết quả |
|---|---|
| V1 không đạt vì lệnh thêm có kỳ vọng âm và chiếm slot | Đúng kết luận, **sai một nửa lý do**: lệnh thêm có lãi trung bình dương. Nghiên cứu 703 ngày là một luật khác (kênh 30, BTC/ETH/SOL, không có slot), nên không áp sang được |
| V2 không đạt nhưng tốt hơn V1, tầng 2 có lãi | Đúng: lãi hơn V1 (+32,0% so với +24,7%) và tầng 2 có lãi, nhưng DD tệ hơn (14,1% so với 8,8%) |
| V3 nhiều lệnh hơn, ngắn hơn, không đạt | Không đạt là đúng, nhưng số lệnh gần như không đổi (325 so với 320) |
| V4 gần với B, không đạt tiêu chí 4 | Đúng cả hai, nhưng V4 hơn B nhiều hơn dự đoán (+6,5 điểm) |
| V5 có ít lệnh thêm | Sai: có 115 lệnh thêm. Năm 2024 chỉ được +2,9% nên DD lên 10,5% |

**Kết luận:** không biến thể nào đạt. Luật đang chạy giữ nguyên, nghiên cứu dừng tại đây đúng như đã đăng ký: không dò thêm ngưỡng hay số nến của V4.

Việc theo dõi V4 trên soak, ghi lại các breakout ADX < 20 mà ADX đang lên trong 3 nến như cách đang làm với limit 0,25 ATR, nằm **ngoài** thiết kế này. Nếu muốn làm thì đó là một quyết định mới của Julius.
