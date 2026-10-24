# Setup-2: vào lệnh sớm bằng nến H1 trong nến H4 (thiết kế nghiên cứu)

Trạng thái: **đã chạy ngày 2026-10-24, không biến thể nào đạt.** Phần thiết kế dưới đây được viết và commit (`44c841e`) trước khi chạy; kết quả ở mục cuối.

Đây là nghiên cứu cục bộ trên dữ liệu đã thấy. Nến được đọc từ database VPS ở chế độ `mode=ro`. Nghiên cứu không đụng tới runtime, active set, ADR-007, soak hay Jev, và không gọi model.

## Vì sao làm

Ngày 23/10 lúc 22:00Z, nến H1 của ZEC giảm từ 1.224,8 xuống 1.158,4 và thủng đáy H1 của 3 ngày trước. Setup-2 không có tín hiệu, vì luật chỉ xét nến H4 đã đóng và kênh Donchian 25.

Julius hỏi có thể kết hợp H1 với H4 để vào lệnh sớm hơn không. Có hai cách hiểu:
- **Cách A:** vẫn là breakout Donchian 25 của H4, nhưng vào ngay khi một nến H1 đóng vượt kênh, không chờ nến H4 đóng.
- **Cách B:** vào theo tín hiệu riêng của H1 trước khi H4 phá kênh. Đây là một hệ thống nhanh hơn và khác hẳn.

Julius chọn thử **cách A**.

**Lý do cách A có thể tốt hơn.** Luật B vào ở giá mở nến H4 kế tiếp, nên thường mua sau khi giá đã chạy xa khỏi kênh. Ví dụ ZEC Spot ngày 21/10: đỉnh kênh khoảng 1.296, nhưng B vào ở 1.341.

**Lý do cách A có thể tệ hơn:**
- Có thêm các lệnh mà B không bao giờ vào: H1 vượt kênh, rồi nến H4 lại đóng ngược vào trong.
- Volume (L3) và EMA/ADX/DMI được đo trên nến H4 chưa đóng.
- Nghiên cứu ngày 19/10 cho thấy nới bộ lọc để vào nhiều hơn (V1, V3, V5) đều trượt tiêu chí.

## Câu hỏi

Trên danh mục slot của ADR-007, vào sớm khi nến H1 đóng vượt kênh Donchian 25 của H4 có **tốt hơn luật hiện tại** về lợi nhuận và về lợi nhuận trên mức sụt giảm không? Kết quả có ổn định ở cả hai nửa dữ liệu, và không phụ thuộc vào một coin không?

## Dữ liệu

- **Coin:** ETH, HYPE, NEAR, SOL, ZEC. Spot chỉ long, Perp chỉ short.
- **Nguồn:** bảng `setup2_candles` trên VPS, xuất bằng `scripts/h1_early_export.py`. Script xuất nến H4 (chuỗi `proxied_bars` mà Setup-2 dùng) và **toàn bộ** nến M15 của mỗi route, kèm volume, cùng anchor, số nến khởi động và mốc proxy của từng coin.
- **Quan sát** được tính lại trên máy bằng chính code của runtime: `setup2.series_observations` (hồ sơ `setup2_slots_v1`) cho nến H4 đã đóng, và `setup2._observe` cho nến H4 đang chạy. Code `setup2.py`, `setup2_store.py` và `donchian_filters.py` ở commit này không khác bản đang chạy trên VPS (`269d6a5`).
- **H1** là 4 nến M15 liên tiếp từ giá mở nến H4. Không dùng nến H1 của sàn.
- **HYPE Spot:** nến dừng ở 2026-10-13 20:00Z vì lỗi `StaleMarketData` (bản sửa `871dd9d` chưa deploy). Route này vào nghiên cứu với dữ liệu tới đó.
- **Giai đoạn:** toàn bộ, từ khi đủ số nến khởi động tới nến H4 cuối lúc lấy dữ liệu, chia làm **hai nửa bằng nhau theo thời gian** như các nghiên cứu trước.

## Các biến thể (cố định từ bây giờ)

**Nến H4 dở dang.** Xét nến H4 `j` đang chạy, sau `h` giờ (`h` = 1, 2, 3). Nến dở dang `P(j, h)` được dựng từ `4h` nến M15 đầu tiên của nến `j`:
- giá mở là giá mở nến `j`;
- giá cao nhất và thấp nhất là của các nến M15 đó;
- giá đóng là giá đóng nến M15 cuối cùng trong số đó;
- volume là tổng volume các nến M15 đó.

Nếu thiếu nến M15 nào thì giờ đó không được xét.

**Kiểm tra sớm.** Thay nến `j` bằng `P(j, h)` rồi chạy đúng `_observe` của Setup-2:
- EMA, ATR, ADX và DMI được tính lại với `P(j, h)` là nến cuối;
- kênh Donchian 25 lấy từ 25 nến đã đóng trước `j`;
- volume profile giống hệt nến `j`, vì profile kết thúc ở giá mở của nến.

Một giờ `h` **đạt** khi giá đóng của `P(j, h)` vượt kênh, không có blocker, và các bộ lọc đạt theo biến thể dưới đây. Lệnh vào ở **giá mở nến H1 kế tiếp**, tức giá mở nến M15 thứ `4h` của nến `j`. Chỉ giờ đạt đầu tiên được dùng. ATR, hệ số size và ADX xếp hạng đều lấy từ quan sát của `P(j, h)`.

| Mã | Luật |
|---|---|
| **B** | Luật hiện tại |
| **E1** Vào sớm, đủ bộ lọc | Kiểm tra sớm với đủ 4 tầng. Riêng volume được **ngoại suy**: volume của `P(j, h)` nhân `4/h` phải lớn hơn 1,2 × MA20. Nếu không giờ nào đạt, nến `j` vẫn được xét lúc đóng như B |
| **E2** Vào sớm, bỏ volume | Như E1, nhưng kiểm tra sớm bỏ tầng volume (L3). Kiểm tra lúc nến đóng vẫn đủ 4 tầng như B |

**Danh mục.** Lệnh vào sớm chỉ được lấy slot còn trống:
- Ở mỗi giá mở H4, các lệnh của B (vào ở giá mở) được chọn trước. Lệnh vào sớm trong nến đó chỉ được xét với slot còn lại.
- Lệnh vào sớm **không được đổi vị thế** (rotation). Nếu muốn vào mà phải đổi một vị thế đang giữ thì bỏ lệnh sớm. Breakout đó vẫn có thể vào như B khi nến đóng, và lúc đó được đổi vị thế như bình thường.
- Lý do: mô phỏng chọn lệnh ở giá mở nến H4, nhưng lệnh vào sớm chỉ biết được ở giữa nến. Cho lệnh sớm giành slot hay đóng vị thế khác ở giá mở là dùng thông tin tương lai.

**Stop:** 3 ATR của `P(j, h)`, tối đa 5% từ giá vào, kéo theo 3 ATR ở mỗi lần nến H4 đóng như cũ. Stop được kiểm trên M15 từ nến M15 vào lệnh tới hết nến `j`.

**Cách vào lệnh:**
- B chạy với hai cách như các nghiên cứu trước: (a) lệnh thị trường; (b) Perp vào bằng limit 0,25 ATR, Spot vẫn lệnh thị trường.
- E1 và E2 chỉ có một cách: vào sớm bằng lệnh thị trường, và khi nến đóng thì vào như B (a). Mỗi biến thể được so với cả hai mốc B, và được coi là cùng một kết quả cho cả hai cách vào, giống S3 của nghiên cứu vùng hỗ trợ/kháng cự.

**Ví dụ ZEC Spot ngày 21/10** (tính nhẩm trên nến Binance công khai):
- Nến H4 12:00–16:00Z vượt đỉnh kênh khoảng 1.296.
- Nếu vào sớm quanh 1.300 thì stop 5% ở khoảng 1.235. Giá xuống 1.187 ngày 22/10, nên lệnh vẫn dừng lỗ −5%.

## Mô phỏng danh mục

Dùng `intraday/replay_v2/slot_replay.py`, giống nghiên cứu ngày 22/10.

- Tối đa 3 vị thế, mỗi coin một vị thế.
- Xếp hạng theo ADX, đổi vị thế khi chênh ADX > 5, có cổng lãi lỗ. Cả ba gọi đúng hàm của runtime.
- Size: 50% vốn mỗi sleeve × 1/3 × hệ số ATR.
- Stop 3 ATR kéo theo giá, tối đa 5%. Thoát Donchian 10 ở giá mở nến kế tiếp.
- Chi phí cả vòng: Perp 0,20%, Spot 0,30%. Không tính funding, không tính luật lỗ 3% mỗi ngày.

Engine được thêm hai thứ, không đổi kết quả của các luật cũ:
- lệnh vào ở giá mở của một nến M15 cho trước trong nến H4 kế tiếp;
- một quan sát riêng để xếp hạng và tính size cho lệnh đó.

## Tiêu chí đạt (cố định trước khi chạy)

Giống 7 tiêu chí của nghiên cứu vùng hỗ trợ/kháng cự. Một biến thể **đạt** nếu đủ cả 7 điều sau:

1. Lợi nhuận tổng cao hơn B ở **cả hai** cách vào lệnh.
2. Mức sụt giảm tối đa ≤ 10% ở cả hai cách vào lệnh.
3. Lợi nhuận chia mức sụt giảm không thấp hơn B, ở cả hai cách vào lệnh.
4. Với lệnh thị trường, lợi nhuận cao hơn B trong **từng** nửa dữ liệu.
5. Với lệnh thị trường và chi phí gấp đôi, lợi nhuận vẫn cao hơn B cũng với chi phí gấp đôi.
6. Với lệnh thị trường, sau khi cắt mọi nến ZEC từ 2026-10-01 ở cả biến thể lẫn B, lợi nhuận vẫn cao hơn B. Lý do: câu hỏi nảy ra từ chart ZEC tháng 10.
7. Với lệnh thị trường, bỏ lần lượt từng coin (5 lần, cả biến thể lẫn B); lần nào lợi nhuận cũng vẫn cao hơn B.

**Nếu cả hai biến thể đạt:** chọn biến thể có lợi nhuận/sụt giảm cao hơn với lệnh thị trường.

**Nếu có biến thể đạt:** viết ADR và một plan riêng để đưa vào luật sau buổi review 28/10. Julius duyệt riêng trước khi bật. Runtime hiện chạy theo nhịp nến H4, nên vào sớm cần thêm một vòng chạy mỗi giờ; tác động lên soak và G5/G6 phải nêu trong ADR.

**Nếu không có biến thể nào đạt:** ghi kết quả vào tài liệu này và dừng. Không dò thêm cách ngoại suy volume, số giờ hay khung M30.

## Đo gì

**Với mỗi biến thể và mỗi cách vào lệnh:**
- lợi nhuận tổng, mức sụt giảm, lợi nhuận/sụt giảm;
- phần của Perp và Spot; số lệnh, lợi nhuận trung bình mỗi lệnh, tỉ lệ thắng;
- lợi nhuận theo năm và theo từng nửa;
- số tín hiệu bị chặn và lý do.

**Riêng E1 và E2:**
- số lần vào sớm, chia theo giờ `h` = 1, 2, 3;
- trong các lệnh vào sớm:
  - bao nhiêu nến H4 cũng đóng thành tín hiệu B. Với những lệnh này, đo **giá vào tốt hơn bao nhiêu** so với giá B lẽ ra vào, tính bằng % và bằng ATR;
  - bao nhiêu nến H4 **không** đóng thành tín hiệu B (phá giả hoặc bộ lọc lúc đóng không đạt), và kết quả của các lệnh đó;
- số lệnh sớm bị bỏ vì không còn slot hoặc vì phải đổi vị thế;
- tỉ lệ lệnh thoát vì chạm stop, so với B.

**Tiêu chí 6–7:** lợi nhuận của từng lần chạy, cho biến thể và cho B.

## Dự đoán (viết trước khi chạy)

- **Giá vào:** với các breakout mà B cũng vào, vào sớm có giá tốt hơn trung bình khoảng 1–2% (0,5–1 ATR).
- **Phá giả:** khoảng 30–45% số lệnh vào sớm rơi vào nến H4 không đóng thành tín hiệu B. Nhóm này lỗ trung bình.
- **E1:** ít lệnh vào sớm. Ngoại suy volume ở giờ đầu rất nhiễu, còn EMA50 và ADX của nến dở dang thường chưa đạt. Kết quả gần B; có thể nhỉnh hơn ở lợi nhuận/sụt giảm nhưng trượt tiêu chí 4 hoặc 7.
- **E2:** nhiều lệnh vào sớm hơn, nhiều phá giả hơn. Lợi nhuận tổng thấp hơn B. Không đạt tiêu chí 1.
- **Tổng thể:** khả năng không có biến thể nào đạt là khoảng 65%.

## Giới hạn

- Dữ liệu đã thấy: 5 coin được chọn sau khi biết kết quả, mẫu khoảng 2,5 năm, không có phần kiểm riêng. Tiêu chí 4, 6 và 7 chỉ thay thế một phần cho phần kiểm riêng.
- Lần thử thứ ba trong một tuần (sau bộ lọc nhanh 19/10 và vùng hỗ trợ/kháng cự 22/10), nên khả năng có biến thể đạt nhờ may mắn tăng lên.
- Lệnh sớm được xếp hạng chung với các lệnh khác ở giá mở nến H4, sau các lệnh của B. Một lệnh sớm có thể giữ chỗ trước một lệnh B của nến đóng sau đó. Ảnh hưởng nhỏ, vì hiếm khi có hơn 3 tín hiệu cùng lúc.
- Giá vào là giá mở nến M15 kế tiếp, không có trượt giá thêm ngoài chi phí cố định. Lúc breakout trượt giá thường lớn hơn.
- Đoạn HYPE Spot dùng giá Perp thay cho dữ liệu Spot chưa có. Ở đoạn đó không có M15 Spot, nên không có lệnh sớm.
- Không có funding, không có Jev, không có luật lỗ 3% mỗi ngày.

## Cách chạy

1. Xuất dữ liệu bằng `scripts/h1_early_export.py` trong container `admin`, chỉ đọc, với `os.nice(10)`.
2. Chạy `intraday/replay_v2/h1_early_study.py`, có test cho từng luật ở `tests/test_h1_early_study.py`.
3. Kiểm mốc trước khi chạy biến thể:
   - quan sát H4 tính trên máy phải trùng với quan sát tính trên VPS ở các nến mẫu;
   - B chạy bằng `slot_replay` sau khi sửa engine phải ra đúng như trước khi sửa.
4. Chạy toàn bộ đúng một lần. Ghi hash dữ liệu, hash cấu hình và kết quả vào mục "Kết quả" dưới đây và vào CHANGELOG.

## Kết quả (2026-10-24)

**Dữ liệu và kiểm mốc:**
- Nến H4 mở tới 04:00Z ngày 24/10, xuất chỉ đọc qua container `admin`. Hash dữ liệu (JSON giải nén) `aba7a4cb…6ce1`; hash cấu hình `cf91032c…d221`; điểm giữa 2025-07-17 08:00Z.
- Quan sát tính lại trên máy trùng với VPS ở **mọi** nến của cả 10 route: breakout, bộ lọc, blocker, cờ thoát, hệ số size.
- B chạy trên engine trước và sau khi sửa ra đúng cùng danh sách lệnh: lệnh thị trường 321 lệnh, +38,67%; limit 305 lệnh, +42,83%.

**Kết quả chính.** E1 và E2 chỉ có một cách vào; cột limit của chúng là cùng kết quả đó.

| | Lợi nhuận | DD | Lợi nhuận/DD | Số lệnh | Thắng | Nửa đầu / nửa sau | Chi phí ×2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| B, lệnh thị trường | +38,7% | 7,8% | 4,98 | 321 | 36,8% | +16,6 / +22,1 | +28,5% |
| B, Perp limit | +42,8% | 5,8% | 7,44 | 305 | 36,7% | +16,9 / +25,9 | |
| E1 vào sớm, đủ bộ lọc | +34,5% | 8,3% | 4,15 | 378 | 35,2% | +15,9 / +18,6 | +22,4% |
| E2 vào sớm, bỏ volume | +35,9% | 7,9% | 4,53 | 387 | 35,4% | +18,5 / +17,4 | +23,5% |

**Tiêu chí:** cả hai trượt tiêu chí 1, 3, 4, 5, 6 và 7, chỉ đạt tiêu chí 2 (DD ≤ 10%).

| | Bỏ ZEC từ 10/2026 | Bỏ ETH | Bỏ HYPE | Bỏ NEAR | Bỏ SOL | Bỏ ZEC |
|---|---:|---:|---:|---:|---:|---:|
| B | +40,0 | +27,7 | +41,1 | +23,5 | +30,6 | +24,4 |
| E1 | +35,8 | +26,1 | +37,0 | +24,6 | +27,8 | +19,9 |
| E2 | +37,2 | +27,7 | +38,8 | +25,3 | +26,6 | +18,4 |

**Phần vào sớm:**

| | E1 | E2 |
|---|---:|---:|
| Giờ đạt kiểm tra sớm (h = 1 / 2 / 3) | 378 / 297 / 251 | 410 / 348 / 297 |
| Lệnh vào sớm thật sự (h = 1 / 2 / 3) | 92 / 71 / 90 | 91 / 81 / 101 |
| Lệnh sớm bị bỏ vì cổng lãi lỗ / hết slot | 60 / 56 | 66 / 59 |
| Nến H4 cũng đóng thành tín hiệu B | 166 lệnh, TB +2,05%, thắng 42% | 168 lệnh, TB +2,26%, thắng 43% |
| Giá vào tốt hơn B ở các lệnh đó | 1,07% (0,46 ATR) | 1,13% (0,47 ATR) |
| Nến H4 **không** đóng thành tín hiệu B | 87 lệnh (34%), TB −0,86%, thắng 25% | 105 lệnh (38%), TB −0,77%, thắng 26% |
| Tỉ lệ lệnh thoát vì stop (B: 88,5%) | 89,2% | 89,7% |

**Phân tích thêm (không đăng ký trước, chỉ để giải thích):**
- Trên 162 breakout mà cả B lẫn E1 đều vào, E1 lãi +41,6 điểm % vốn, B chỉ +25,3. E2 so với B là +48,1 và +31,2. **Vào sớm thật sự cho giá tốt hơn và lãi hơn trên cùng một breakout.**
- Phần lãi đó bị mất ở hai chỗ:
  - **Phá giả:** lệnh vào sớm mà nến H4 không đóng thành tín hiệu lỗ −5,7 (E1) và −8,0 (E2).
  - **Chiếm slot:** các lệnh phá giả giữ slot và đẩy cổng lãi lỗ đóng nhiều hơn (B 10 lần, E1 25 lần). Vì vậy E1 mất 52 lệnh mà B có, tổng +13,0; E2 mất 61 lệnh, tổng +9,6.
- Kết quả không ổn định giữa hai nửa: E2 hơn B ở nửa đầu (+18,5 so với +16,6) nhưng kém ở nửa sau (+17,4 so với +22,1).

**So với dự đoán:**
- Đúng:
  - giá vào tốt hơn khoảng 1% (đoán 1–2%);
  - phá giả 34–38% số lệnh sớm (đoán 30–45%), và nhóm này lỗ;
  - E2 lợi nhuận thấp hơn B;
  - không biến thể nào đạt.
- Sai: E1 không hề ít lệnh sớm. Ngoại suy volume ×4/h dễ đạt ngưỡng ở giờ đầu, nên E1 có gần bằng số lệnh sớm của E2.

**Kết luận:**
- Luật Setup-2 giữ nguyên, không có ADR.
- Theo thiết kế, dừng ở đây: không dò thêm cách ngoại suy volume, số giờ hay khung M30.
- Lợi thế về giá vào là có thật, nhưng không bù được phá giả và việc chiếm slot của danh mục 3 vị thế.
