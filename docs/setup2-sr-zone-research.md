# Setup-2: vùng hỗ trợ/kháng cự và cách vào lệnh (thiết kế nghiên cứu)

Trạng thái: **thiết kế, chưa chạy.** Tài liệu này được viết và commit trước khi chạy. Kết quả sẽ được ghi ở mục cuối.

Đây là nghiên cứu cục bộ trên dữ liệu đã thấy. Nến và quan sát Setup-2 được đọc từ database VPS ở chế độ `mode=ro`. Nghiên cứu không đụng tới runtime, active set, ADR-007, soak hay Jev, và không gọi model.

## Vì sao làm

Ngày 21/10, Setup-2 vào 2 lệnh ZEC Spot LONG: ở 1341,46 (nến đóng 16:00Z) và 1357,03 (nến đóng 20:00Z).

- Cả hai lệnh vào ngay dưới vùng kháng cự 1349–1385. Trong tháng 10, giá đã bị chặn ở vùng này 5 lần: 1368 ngày 4/10, 1366 ngày 5/10, 1385 ngày 6/10, 1348 ngày 7/10, 1349 ngày 12/10.
- Breakout lên tới 1368,4, gần đúng bằng đỉnh 1368,09 ngày 4/10, rồi giá quay đầu.
- Cả hai lệnh chạm mức stop tối đa 5%: lệnh thứ hai ở nến đóng 04:00Z, lệnh thứ nhất ở nến đóng 08:00Z ngày 22/10.
- Tính tới 22/10, soak có 6 lệnh đã đóng và cả 6 đều thua.

Kênh Donchian 25 nến chỉ nhìn lại khoảng 4 ngày, nên không thấy các đỉnh cũ đó.

Ngày 22/10, Julius đưa ra 3 indicator TradingView. Các phép thử (script ở scratchpad, chưa đưa vào repo; nến H4 Spot Binance công khai, 5 coin, từ 11/2025, chưa tính phí) cho kết quả:

| Indicator | Cách tìm vùng | Tín hiệu phá vùng |
|---|---|---|
| LuxAlgo, Support and Resistance Levels with Breaks | Mỗi bên 1 mức (pivot 15/15). Bỏ qua mọi vùng gần giá. Xác nhận trễ 64 giờ | Không có lợi thế: t từ −1,76 đến +1,34 |
| LonesomeTheBlue, Support Resistance Channels | Nhiều vùng, có độ mạnh, trễ 40 giờ. Gần như trùng với vùng tìm bằng tay trên ZEC | Không có lợi thế: phá kháng cự +0,45% sau 12 nến, giá tự tăng trung bình +0,44% |
| ChartPrime, Support and Resistance (High Volume Boxes) | Mỗi bên 1 hộp, trễ 80 giờ | Break Res +4,57% sau 12 nến, t = 2,35, 27 lệnh. Nhưng đây là 1 trong khoảng 14 lần thử, và kết quả dồn vào 3 lệnh lớn |

Kết luận lúc đó:
- Nên dùng SRchannel để tìm vùng.
- Tín hiệu phá vùng không dùng được. Giả thuyết đáng kiểm là: **breakout có vùng cản ngay phía trước thì thua nhiều hơn.**

Julius chọn thử cả 4 cách vào lệnh dưới đây. Nếu có cách đạt, nó được đưa vào luật Setup-2 sau buổi review soak 28/10, qua một ADR và một lần Julius duyệt riêng.

## Câu hỏi

1. Các vùng SRchannel có thật sự chặn giá hơn một vùng ngẫu nhiên cùng độ rộng không?
2. Trên danh mục slot của ADR-007 (5 coin, Spot long, Perp short, tối đa 3 vị thế), có cách vào lệnh nào dựa trên vùng mà **tốt hơn luật hiện tại** về lợi nhuận và về lợi nhuận trên mức sụt giảm, ổn định ở cả hai nửa dữ liệu, và không phụ thuộc vào một coin không?

## Dữ liệu

- **Coin:** ETH, HYPE, NEAR, SOL, ZEC. Spot chỉ long, Perp chỉ short.
- **Nguồn:** bảng `setup2_candles` trên VPS. Quan sát được tính bằng chính `setup2.series_observations` (hồ sơ `setup2_slots_v1`), giống nghiên cứu bộ lọc nhanh ngày 19/10. Vì vậy breakout, bộ lọc, blocker, cờ thoát Donchian, ATR và hệ số size đều đúng như runtime đang tính.
- **Vùng:** `intraday/replay_v2/sr_zones.py`, bản port SRchannel (MPL-2.0) với **tham số mặc định, không dò**:
  - pivot 10 nến mỗi bên trên giá cao nhất/thấp nhất;
  - độ rộng vùng tối đa 5% biên độ 300 nến;
  - nhìn lại 290 nến; 6 vùng mạnh nhất.

  Vùng của nến `t` chỉ dùng các nến tới `t`. Vùng chỉ được tính lại khi có pivot mới được xác nhận. Vùng được tính trên đúng chuỗi H4 mà Setup-2 dùng cho route đó.
- **M15:** cho mọi breakout Donchian không blocker, ở cả hai thị trường, lấy 13 nến H4 sau breakout (208 nến M15). Dùng để mô phỏng lệnh limit.
- **Giai đoạn:** toàn bộ, từ khi đủ số nến khởi động tới nến H4 cuối lúc lấy dữ liệu, chia làm **hai nửa bằng nhau theo thời gian** như nghiên cứu ngày 19/10.

## Phần 0: vùng có chặn giá không

Phần này chỉ để mô tả, không phải tiêu chí.

- **Một lần chạm:** xét vùng `Z` đã biết ở nến `t−1`.
  - Kháng cự: nến `t−1` đóng dưới `Z.lo`, và giá cao nhất của nến `t` ≥ `Z.lo`.
  - Hỗ trợ: đối xứng (nến `t−1` đóng trên `Z.hi`, giá thấp nhất của nến `t` ≤ `Z.hi`).
- **Kết cục:** xét lần lượt giá đóng của nến `t` tới `t+3`.
  - Với kháng cự: nếu gặp giá đóng `< Z.lo − 0,5·ATR` trước, là **bị chặn**. Nếu gặp giá đóng `> Z.hi` trước, là **vượt**. Không gặp cả hai thì **không rõ**.
  - Hỗ trợ đối xứng. ATR là ATR14 của nến `t`.
- **Vùng giả:** mỗi lần bộ vùng được tính lại, mỗi vùng thật sinh một vùng giả.
  - Vùng giả cùng độ rộng; tâm bằng giá đóng của nến đó nhân `(1+u)`, với `u` ngẫu nhiên đều trong ±10%.
  - Nếu vùng giả chồng lên vùng thật thì rút lại, tối đa 100 lần.
  - Seed `20261022`; các route được xử lý theo thứ tự tên.
  - Vùng giả được giữ cho tới lần tính lại kế tiếp, giống vùng thật.
- **Báo cáo:** số lần chạm và tỉ lệ bị chặn, vượt, không rõ của vùng thật và vùng giả, theo từng route và gộp lại.

## Các biến thể (cố định từ bây giờ)

**Ký hiệu:**
- `s` là chiều lệnh: +1 với long Spot, −1 với short Perp.
- `c` là giá đóng của nến breakout `t`. `ATR` là ATR14 của Setup-2 ở nến đó.
- **Vùng chặn** của một breakout:
  - vùng chứa `c`, nếu có (`inside`);
  - nếu không, vùng gần nhất nằm hẳn phía trước (`ahead`), nếu mép gần của nó cách `c` dưới **1 ATR**. Long: `Z.lo − c < ATR`. Short: `c − Z.hi < ATR`.
- Mức 1 ATR được chọn trước, tương đương biên độ một nến H4 thông thường. Không dò mức này.

Mọi biến thể vẫn cần đủ điều kiện như luật hiện tại: breakout Donchian 25, đủ bộ lọc, không có blocker. Biến thể chỉ đổi cách vào lệnh hoặc stop.

| Mã | Luật |
|---|---|
| **B** | Luật hiện tại |
| **S1** Lọc | Như B, nhưng bỏ breakout có vùng chặn |
| **S2** Chờ vượt hẳn | Breakout không có vùng chặn: vào như B. Breakout có vùng chặn `Z`: chờ tối đa 6 nến H4 (`t+1` tới `t+6`). Ở nến **đầu tiên** `k` thoả cả ba điều kiện thì vào ở giá mở nến `k+1`: (1) giá đóng vượt mép xa của `Z` (long: `> Z.hi`; short: `< Z.lo`); (2) ở nến `k` không có vùng chặn mới, tính với giá đóng và ATR của nến `k`; (3) nến `k` không có blocker. Huỷ nếu có cờ thoát Donchian ở bất kỳ nến nào từ `t+1` tới `k`. Lệnh được xếp hạng và tính size theo quan sát của nến `k`, và ghi sổ dưới nến breakout `t`. Nếu tới nến `k` mà route hoặc coin đang bận, lệnh bị bỏ |
| **S3** Hồi về vùng | Breakout không có vùng chặn: vào như B. Breakout có vùng chặn: chờ như S2 tới nến `k`. Sau đó đặt **limit ở mép vùng vừa vượt** (long: `Z.hi`; short: `Z.lo`), hiệu lực 6 nến (`k+1` tới `k+6`), đọc khớp trên M15. Stop ban đầu: long `Z.lo − 0,25·ATR_k`, short `Z.hi + 0,25·ATR_k`, tối đa 5% từ giá khớp. Sau đó stop kéo theo 3 ATR như cũ, chỉ khi mức đó chặt hơn. Không khớp thì không có lệnh |
| **S4** Stop theo vùng | Vào lệnh như B, kể cả breakout có vùng chặn. Lấy vùng gần nhất nằm hẳn phía sau `c` (`behind`). Stop ban đầu: long `Z.lo − 0,25·ATR`, short `Z.hi + 0,25·ATR`. Nếu không có vùng, hoặc stop đó cách `c` quá 5%, thì dùng 3 ATR như cũ. Stop vẫn tối đa 5% từ giá vào, và kéo theo 3 ATR chỉ khi mức đó chặt hơn |

**Cách vào lệnh:**
- B, S1, S2 và S4 chạy với hai cách như nghiên cứu ngày 19/10:
  - (a) lệnh thị trường ở giá mở nến kế tiếp;
  - (b) Perp vào bằng limit 0,25 ATR trên giá đóng của nến dùng để vào (nến breakout với B, S1, S4; nến `k` với S2), chờ 1 nến, đọc khớp trên M15. Spot vẫn vào bằng lệnh thị trường.
- S3 có một cách duy nhất như mô tả ở bảng. Nó được so với cả hai mốc B (a) và B (b), và được coi là cùng một kết quả cho cả hai cách vào.

**Ví dụ ZEC ngày 21/10** (tính trên nến Binance công khai, chưa phải dữ liệu VPS):
- Nến đóng 16:00Z (giá đóng 1341,46): vùng 1349,3–1385 cách 7,8, chưa tới 1 ATR (khoảng 45), nên là vùng chặn.
- Nến đóng 20:00Z (giá đóng 1357,03): giá đóng nằm trong vùng đó.
- S1 bỏ cả hai lệnh. S2 và S3 không vào, vì trong 6 nến sau đó không có nến nào đóng trên 1385.

## Mô phỏng danh mục

Dùng `intraday/replay_v2/slot_replay.py`. Đây là vòng lặp danh mục của nghiên cứu bộ lọc nhanh, được tách ra và kiểm là cho đúng cùng danh sách lệnh với bản cũ.

- Tối đa 3 vị thế, mỗi coin một vị thế.
- Xếp hạng theo ADX; đổi vị thế khi chênh ADX > 5; có cổng lãi lỗ. Cả ba đều gọi đúng hàm của runtime.
- Size: 50% vốn mỗi sleeve × 1/3 × hệ số ATR.
- Stop 3 ATR kéo theo giá, tối đa 5%. Thoát Donchian 10 ở giá mở nến kế tiếp. Giá mở nhảy qua stop thì thoát ở giá mở.
- Chi phí cả vòng: Perp 0,20%, Spot 0,30%. Không tính funding, không tính luật lỗ 3% mỗi ngày.

## Tiêu chí đạt (cố định trước khi chạy)

Một biến thể **đạt** nếu đủ cả 7 điều sau:

1. Lợi nhuận tổng cao hơn B ở **cả hai** cách vào lệnh.
2. Mức sụt giảm tối đa ≤ 10% ở cả hai cách vào lệnh.
3. Lợi nhuận chia mức sụt giảm không thấp hơn B, ở cả hai cách vào lệnh.
4. Với lệnh thị trường, lợi nhuận cao hơn B trong **từng** nửa dữ liệu.
5. Với lệnh thị trường và chi phí gấp đôi, lợi nhuận vẫn cao hơn B cũng với chi phí gấp đôi.
6. Với lệnh thị trường, sau khi cắt mọi nến ZEC từ 2026-10-01 ở cả biến thể lẫn B, lợi nhuận vẫn cao hơn B. Lý do: giả thuyết nảy ra từ chart ZEC tháng 10.
7. Với lệnh thị trường, bỏ lần lượt từng coin (5 lần, cả biến thể lẫn B); lần nào lợi nhuận cũng vẫn cao hơn B.

Tiêu chí 1–5 giống nghiên cứu ngày 19/10. Tiêu chí 6–7 được thêm vì lần này kết quả có thể đi thẳng vào luật.

**Nếu nhiều biến thể cùng đạt:** chọn biến thể có lợi nhuận/sụt giảm cao nhất với lệnh thị trường.

**Nếu có biến thể đạt:** viết ADR-008 và một plan riêng để đưa vào luật sau buổi review 28/10. Julius duyệt riêng trước khi bật. Thử 4 biến thể cùng lúc thì khả năng có một biến thể đạt nhờ may mắn không nhỏ; tiêu chí 6–7 chỉ giảm một phần rủi ro đó.

**Nếu không có biến thể nào đạt:** ghi kết quả vào tài liệu này và dừng. Không dò thêm mức ATR, số nến chờ hay tham số của vùng.

## Đo gì

**Với mỗi biến thể và mỗi cách vào lệnh:**
- lợi nhuận tổng, mức sụt giảm, lợi nhuận/sụt giảm;
- phần của Perp và Spot; số lệnh, lợi nhuận trung bình mỗi lệnh, tỉ lệ thắng;
- lợi nhuận theo năm và theo từng nửa;
- số tín hiệu bị chặn và lý do.

**Riêng từng biến thể:**
- Mọi biến thể: số breakout có vùng chặn, và kết quả của chính những lệnh đó trong B. Đây là phần S1 bỏ đi.
- S2 và S3: số lần chờ được tới nến `k`, số lần huỷ, và với S3 thêm tỉ lệ khớp limit. Kết quả của các lệnh vào muộn.
- S4: số lần stop theo vùng chặt hơn và rộng hơn 3 ATR; tỉ lệ lệnh thoát vì chạm stop so với B.

**Tiêu chí 6–7:** lợi nhuận của từng lần chạy, cho biến thể và cho B.

## Dự đoán (viết trước khi chạy)

- **Phần 0:** vùng thật chặn giá nhiều hơn vùng giả nhưng không nhiều, khoảng 5–10 điểm phần trăm. Trên H4 crypto, các mức cũ bị phá thường xuyên.
- **S1:**
  - Bỏ khoảng 25–40% số lệnh của B. Lý do: breakout 25 nến thường đóng sát một đỉnh hoặc đáy cũ ngoài kênh.
  - Tỉ lệ thắng tăng, nhưng lợi nhuận tổng thấp hơn B, vì bỏ mất cả một số cú chạy dài phá qua vùng cũ. Không đạt tiêu chí 1.
- **S2:** gần với B nhất. Có thể có lợi nhuận/sụt giảm tốt hơn, nhưng trượt tiêu chí 4 hoặc 7.
- **S3:** ít lệnh hơn B nhiều, vì breakout mạnh thường không quay lại kiểm tra vùng. Không đạt tiêu chí 1.
- **S4:** stop theo vùng thường chặt hơn 3 ATR, nên bị quét nhiều hơn. Lợi nhuận thấp hơn B.
- **Tổng thể:** khả năng không có biến thể nào đạt là khoảng 60%.

## Giới hạn

- Dữ liệu đã thấy, 5 coin được chọn sau khi biết kết quả, mẫu khoảng 2,5 năm, và không có phần kiểm riêng. Tiêu chí 4, 6 và 7 chỉ thay thế một phần cho phần kiểm riêng.
- Ý tưởng sinh ra từ 2 lệnh ZEC tháng 10/2026, nằm trong dữ liệu.
- Thử 4 biến thể, sau khoảng 14 phép thử indicator ngày 22/10.
- Không có funding, không có Jev, không có luật lỗ 3% mỗi ngày.
- Đoạn HYPE Spot dùng giá Perp thay cho dữ liệu Spot chưa có: vùng được tính trên chuỗi đó. Runtime chặn lệnh khi cửa sổ volume chạm đoạn này, và mô phỏng giữ đúng blocker đó.
- Vùng được tính bằng float. Nếu đưa vào runtime, cần kiểm lại rằng bản Decimal ra cùng kết quả.

## Cách chạy

1. Xuất dữ liệu bằng `scripts/sr_zone_export.py` trong container `admin`, chỉ đọc, với `os.nice(10)`.
2. Chạy `intraday/replay_v2/sr_zone_study.py`, có test cho từng luật ở `tests/test_sr_zone_study.py`.
3. Trước khi chạy các biến thể, kiểm mốc: chạy B bằng `slot_replay` và bằng bản `fast_filter_study.run` trước khi tách code, trên cùng dữ liệu. Hai bên phải ra đúng cùng danh sách lệnh.
4. Chạy toàn bộ đúng một lần. Ghi hash dữ liệu, hash cấu hình và kết quả vào mục "Kết quả" dưới đây và vào CHANGELOG.
