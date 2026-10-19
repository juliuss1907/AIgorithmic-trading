# Setup-3: bán khi giá hồi lên vùng cản trong xu hướng giảm (thiết kế nghiên cứu)

Trạng thái: **đã chạy ngày 2026-10-19, không có chiều nào đạt.** Phần thiết kế dưới đây được viết và commit (`0bafd61`) trước khi chạy; kết quả ở mục cuối.

Đây là nghiên cứu cục bộ trên dữ liệu đã thấy. Dữ liệu nến được đọc từ database VPS ở chế độ `mode=ro`. Nghiên cứu không đụng tới runtime, active set, ADR-007, soak hay Jev, và không gọi model.

## Vì sao làm

Ngày 18/10, Setup-2 short ZEC Perp ở nến 20Z, đúng lúc giá vừa phá đáy kênh 25 nến. Giá bật lên ngay và chạm stop sau khoảng hai giờ.

Julius chỉ ra trên chart H4 của ZEC (12/10–19/10):
- mỗi lần giá hồi lên gần EMA200 hoặc chạm kênh Donchian trên thì quay đầu giảm;
- mỗi lần giá chạm kênh dưới thì hồi lên.

Các phép kiểm ngày 19/10 (script ở scratchpad, chưa đưa vào repo) cho thấy:
- Setup-2 short Perp gần như hoà sau phí: +0,06% đến +0,20% mỗi lệnh, tỉ lệ thắng khoảng 36%.
- Lọc theo khoảng cách tới EMA50 hay EMA200, hoặc chờ một nến xác nhận, đều không cải thiện.
- Điều Julius thấy (bán khi giá hồi lên vùng cản) diễn ra ở một thời điểm khác hẳn lúc Setup-2 vào lệnh. Lúc breakout, giá thường đã cách EMA200 khoảng 4,5 ATR. Vì vậy điều này phải được kiểm như một setup riêng, không thể gắn thành bộ lọc của Setup-2.

Julius cũng gửi một bài trên Binance Square: [Crypto_Eagle_Queen, 21/05/2025](https://www.binance.com/vi/square/post/24572962695049).
- Luật trong bài: EMA50 cắt lên EMA200, chờ giá hồi lại chạm EMA200 rồi bật lên thì vào lệnh, thoát khi giá đóng dưới EMA50, stop dưới điểm vào.
- Bài viết không kèm số liệu kiểm chứng nào, nên ở đây nó chỉ là một giả thuyết.

## Câu hỏi

1. Trong xu hướng rõ ràng, bán khi giá hồi lên vùng cản (EMA200 hoặc kênh trên), hay mua khi giá về vùng hỗ trợ trong xu hướng tăng, có lãi sau phí trên nến H4 của 5 coin không?
2. Luật giao cắt EMA50/200 rồi chờ giá hồi về EMA200, như trong bài viết, có lãi sau phí không?
3. Nếu có lãi thì Setup-3 có **bù** được cho Setup-2 không? Cụ thể: lãi lỗ theo tháng của hai setup có tương quan thấp không, và Setup-3 có lãi trong những tháng Setup-2 lỗ không?

## Dữ liệu

- **Coin:** ETH, HYPE, NEAR, SOL, ZEC, tức 5 coin của ADR-007.
- **Nguồn:** bảng `setup2_candles` trên VPS, gồm H4 và M15 của cả Perp và Spot. HYPE Spot chỉ dùng dữ liệu của chính nó; các nến Perp dùng thay chỗ (khối lượng bằng 0) không được tính.
- **Chỉ báo:** dùng lại `replay_v2.donchian_filters.indicator_series`, tức đúng EMA50, EMA200 và ATR14 mà Setup-2 đang dùng, với cùng mốc và số nến khởi động như active set.
- **Chia giai đoạn:**
  - phần dò: từ khi đủ số nến khởi động (khoảng 2024-04-10) tới 2025-12-31;
  - phần kiểm: từ 2026-01-01 tới nến H4 cuối cùng lúc lấy dữ liệu.

  Phần kiểm chỉ được chạy **một lần**, với đúng cấu hình đã chọn trên phần dò.
- **Lưu ý về phần kiểm:** giả thuyết H-A nảy ra từ chart ZEC tháng 10/2026, tức nằm trong phần kiểm. Vì vậy phần kiểm được báo cáo thêm một lần sau khi bỏ ZEC từ 2026-10-01 trở đi.

## Luật (viết cho short Perp; long Spot là bản đối xứng, đổi trên ↔ dưới)

Spot chỉ được long, Perp chỉ short, giống hệ thống hiện tại. Gọi `s` là chiều lệnh: −1 với short, +1 với long. Mọi điều kiện dưới đây được tính trên **nến H4 đã đóng** `t`.

**Xu hướng (chung cho cả hai giả thuyết):**
- EMA50 nằm dưới EMA200;
- EMA200 dốc xuống: EMA200 tại `t` thấp hơn EMA200 tại `t−6`, tức 24 giờ trước.

### H-A: giá hồi lên vùng cản rồi bị từ chối (từ chart của Julius)

**Chạm vùng cản.** Nến `t` chạm vùng cản nếu đáp ứng một trong hai điều kiện, tuỳ biến thể:
- (Z1) giá cao nhất của nến ≥ EMA200 − z·ATR;
- (Z2) giá cao nhất của nến ≥ đỉnh kênh 25 nến tính tới nến `t−1`.

**Bị từ chối.** Nến `t` đáp ứng đủ ba điều kiện:
- giá đóng dưới EMA200;
- là nến đỏ (giá đóng dưới giá mở);
- giá đóng nằm ở nửa dưới thân nến, tức giá đóng ≤ (giá cao nhất + giá thấp nhất)/2.

**Vào lệnh.** Ở giá mở của nến `t+1`.

**Không vào lệnh** khi:
- route đó đang có vị thế Setup-3;
- hoặc stop cách giá vào quá 5% (giữ đúng mức lỗ tối đa của ADR-007);
- hoặc stop cách giá vào dưới 0,5 ATR (lệnh quá sát, phí chiếm phần lớn rủi ro).

**Stop.** Giá cao nhất của nến từ chối, cộng 0,25 ATR. Stop cố định, không kéo theo giá.

**Chốt lời (T), tuỳ biến thể:**
- (T1) đáy kênh 25 nến tính tới nến `t`, đặt lệnh limit ngay từ lúc vào;
- (T2) mức 2R, tức gấp đôi khoảng cách từ giá vào tới stop.

**Thoát theo thời gian.** Sau 30 nến (5 ngày), thoát ở giá mở của nến kế tiếp.

**Khi stop và chốt lời cùng bị chạm trong một nến H4:** đọc nến M15 để biết mức nào bị chạm trước. Nếu một nến M15 chạm cả hai thì tính là chạm stop.

### H-B: giao cắt EMA50/200 rồi chờ giá hồi về EMA200 (theo bài viết)

- **Giao cắt:** EMA50 cắt xuống EMA200 ở nến `c`. Bài viết không yêu cầu điều kiện xu hướng nào thêm.
- **Giá hồi về EMA200:** tìm nến **đầu tiên** `t` trong khoảng `c+1` tới `c+30` có giá cao nhất ≥ EMA200 và giá đóng < EMA200. Không có nến nào như vậy thì không vào lệnh. Mỗi lần giao cắt chỉ cho một lệnh.
- **Vào lệnh:** ở giá mở của nến `t+1`.
- **Stop:** giống H-A (giá cao nhất của nến `t` cộng 0,25 ATR), cùng các giới hạn 5% và 0,5 ATR.
- **Thoát:** khi một nến đóng **trên** EMA50 thì thoát ở giá mở của nến kế tiếp, đúng như bài viết. Ngoài ra vẫn thoát khi chạm stop. Không có mức chốt lời cố định.
- **Không có tham số dò.** H-B được chạy đúng như trên ở cả hai giai đoạn.

## Các biến thể dò của H-A và cách chọn

Có 6 biến thể, ghép từ các lựa chọn sau:
- vùng cản: Z1 với z = 0,5; Z1 với z = 1,0; hoặc Z2;
- chốt lời: T1 hoặc T2.

Mỗi chiều (short Perp, long Spot) được chọn riêng.

**Cách chọn biến thể trên phần dò** (cố định từ bây giờ):
- Chỉ xét biến thể có ít nhất 30 lệnh.
- Chọn biến thể có lãi ròng trung bình mỗi lệnh cao nhất.
- Nếu hai biến thể chênh nhau ít hơn một sai số chuẩn thì chọn biến thể có ít lệnh hơn. Đây là một quy tắc ngắt hoà đơn giản để giảm rủi ro chọn được biến thể đẹp nhờ may mắn.

## Chi phí và mức vào lệnh

- **Phí:** dùng cùng hồ sơ chi phí với replay: Perp 5 bps phí + 5 bps trượt giá mỗi chiều, Spot 10 + 5 bps. Riêng lệnh chốt lời T1 đặt limit vẫn bị tính phí taker, để thận trọng.
- **Funding:** database VPS chưa có lịch sử funding. Kết quả short được báo cáo thêm một lần với phí funding cố định 0,01% mỗi 8 giờ trả cho phía short (dương là short nhận) và một lần với −0,01%, để thấy độ nhạy.
- **Khối lượng khi tính danh mục:** mỗi lệnh có cùng mức rủi ro, 1% vốn khi chạm stop, và tối đa 3 vị thế cùng lúc. Kết quả chính được đo bằng % mỗi lệnh và theo R; danh mục chỉ dùng để mô tả.

## Đo gì

Với mỗi giả thuyết, mỗi chiều, và mỗi giai đoạn (dò, kiểm):
- **Kết quả mỗi lệnh:** số lệnh, tỉ lệ thắng, lãi trung bình của lệnh thắng và lỗ trung bình của lệnh thua, lãi ròng trung bình mỗi lệnh (% và R), sai số chuẩn, t, profit factor.
- **Theo nhóm:** theo coin và theo năm; lý do thoát (stop, chốt lời, hết thời gian, giao cắt EMA50).
- **Khi chi phí tăng gấp đôi:** lãi ròng có còn dương không.
- **Danh mục mô tả:** lãi ròng, mức sụt giảm tối đa, số vị thế mở trung bình.
- **So với Setup-2:**
  - tương quan lãi lỗ theo tháng với Setup-2 trên cùng 5 coin, dùng replay danh mục slot ngày 19/10 làm Setup-2;
  - lãi lỗ của Setup-3 trong các tháng Setup-2 lỗ;
  - số lần Setup-3 và Setup-2 cùng có lệnh trên một coin.

## Tiêu chí đạt (cố định trước khi chạy)

Mỗi chiều được xét riêng. Một chiều **đạt** nếu đủ cả 6 điều sau:

1. Phần dò: lãi ròng trung bình mỗi lệnh lớn hơn 0 với t ≥ 2.
2. Phần kiểm: có ít nhất 20 lệnh và lãi ròng trung bình mỗi lệnh lớn hơn 0. Nếu dưới 20 lệnh thì kết luận là "chưa đủ mẫu", không tính là đạt.
3. Cả hai giai đoạn vẫn dương khi chi phí tăng gấp đôi.
4. Ít nhất 3 trên 5 coin có lãi ròng dương, tính gộp hai giai đoạn.
5. Kết quả phần kiểm vẫn dương sau khi bỏ ZEC từ 2026-10-01.
6. Tương quan lãi lỗ theo tháng với Setup-2 dưới 0,5.

**Nếu đạt:** đề xuất soak song song, chỉ ghi tín hiệu, không đặt lệnh, theo khuôn của soak Setup-2. Việc này cần một kế hoạch riêng và Julius duyệt riêng. Nghiên cứu này không bật bất cứ thứ gì.

**Nếu không đạt:** ghi kết quả vào tài liệu này và dừng. Không dò thêm biến thể trên phần kiểm.

## Dự đoán (viết trước khi chạy)

- **H-B (theo bài viết) không đạt.**
  - Giao cắt EMA50/200 trên H4 hiếm, ước chừng 2–4 lần mỗi coin mỗi năm, nên phần kiểm dễ dưới 20 lệnh.
  - Thoát khi giá đóng qua EMA50 thường xảy ra sớm, vì sau khi chạm EMA200 giá phải đi lại một quãng mới về phía bên kia EMA50.
- **H-A short Perp:**
  - Phần dò có lãi nhỏ, khoảng +0,3% đến +0,8% mỗi lệnh, tỉ lệ thắng trên 45%, nhưng có thể không đạt mức t ≥ 2.
  - Biến thể Z2 (chạm kênh trên) có ít lệnh hơn Z1 nhưng tốt hơn mỗi lệnh.
  - Trong các giai đoạn trend mạnh, ví dụ cú giảm cuối năm 2024, H-A lỗ liên tiếp vì giá không hồi lên tới vùng cản mà cứ thế đi tiếp.
- **H-A long Spot** yếu hơn short. Lý do: các đợt tăng của crypto ít hồi về EMA200; khi hồi về tới đó thì xu hướng thường đã gãy.
- **Tương quan với Setup-2** thấp hoặc âm. Đây là lý do duy nhất để giữ Setup-3 ngay cả khi lãi riêng của nó chỉ nhỏ.

## Giới hạn

- Dữ liệu đã thấy, 5 coin được chọn sau khi biết kết quả, và mẫu chỉ khoảng 2,5 năm.
- Giả thuyết H-A sinh ra từ một chart nằm trong phần kiểm. Tiêu chí 5 giảm một phần rủi ro này, nhưng không loại bỏ được hết.
- Không có Jev, không có funding thật, và không có cạnh tranh slot với Setup-2.
- Thiết kế không thử stop kéo theo giá hay vào lệnh bằng limit, để giới hạn số biến thể.

## Cách chạy (dự kiến)

1. Xuất nến H4 và M15 cùng các chỉ báo từ VPS bằng một script chỉ đọc chạy trong container `admin`, giống phép kiểm ngày 19/10.
2. Viết `intraday/replay_v2/setup3_study.py`, có test cho từng luật: vùng cản, từ chối, giao cắt, stop và chốt lời đọc trên M15, thoát theo thời gian.
3. Chạy phần dò, ghi lại biến thể được chọn kèm hash của cấu hình, sau đó mới chạy phần kiểm.
4. Ghi kết quả vào mục "Kết quả" dưới đây và vào CHANGELOG.

## Kết quả (2026-10-19)

**Code và dữ liệu:**
- Code: `intraday/replay_v2/setup3_study.py`, test ở `tests/test_setup3_study.py` (22 test).
- Dữ liệu: xuất bằng `scripts/setup3_export.py` lúc 07:22Z, gồm các nến tới nến H4 mở lúc 04:00Z ngày 19/10.
- Hash dữ liệu: `471e56db…2b8bd776`. Hash cấu hình: `7d5c2820…07fd03a5`.
- Giai đoạn kiểm chỉ chạy được khi cả hai hash khớp với lúc dò, và đã chạy đúng một lần.

**Những chỗ thiết kế chưa nói rõ, được quyết khi viết code (trước khi chạy):**
- Nếu mức chốt lời đã nằm ở phía sau giá vào lệnh (ví dụ đáy kênh cao hơn giá khớp lệnh short), lệnh bị bỏ vì không còn khoảng để chốt lời.
- Nếu giá mở cửa nhảy qua mức chốt lời, lệnh thoát ở giá mở.
- HYPE Spot: chỉ báo được tính trên chuỗi giá có dùng giá Perp thay cho đoạn chưa có dữ liệu Spot. Setup-3 không dùng khối lượng nên việc này không ảnh hưởng. Tín hiệu chỉ được tính từ 2026-09-24, khi có dữ liệu Spot thật.
- H-B: sau khi EMA50 cắt EMA200, chỉ lấy lần chạm EMA200 đầu tiên trong 30 nến, kể cả khi EMA50 cắt ngược lại trước đó. Đây đúng là luật như bài viết mô tả.
- Mốc so sánh Setup-2 là bản mô phỏng danh mục slot ngày 19/10 (320 lệnh, tổng +39,3%). Bản này nằm ngoài repo; danh sách lệnh của nó được đưa vào làm input.

**Giai đoạn dò** (lệnh vào trước 2026). Cách chọn đã đăng ký chọn được:
- short Perp: `A:ema200-0.5atr:channel`;
- long Spot: `A:channel:channel`, dù cả 6 biến thể Spot đều âm.

| Short Perp, giai đoạn dò | Số lệnh | Tỉ lệ thắng | TB mỗi lệnh | t |
|---|---|---|---|---|
| Vùng EMA200 − 0,5 ATR, chốt ở đáy kênh | 108 | 39% | +0,85% | 1,76 |
| Vùng EMA200 − 0,5 ATR, chốt 2R | 118 | 45% | +0,76% | 1,90 |
| Vùng EMA200 − 1 ATR, chốt ở đáy kênh | 163 | 40% | +0,72% | 1,90 |
| Vùng EMA200 − 1 ATR, chốt 2R | 178 | 41% | +0,54% | 1,67 |
| Chạm kênh trên, chốt ở đáy kênh | 79 | 22% | −0,42% | −0,77 |
| Chạm kênh trên, chốt 2R | 79 | 34% | −0,13% | −0,27 |
| H-B (theo bài viết) | 14 | 36% | +1,05% | 1,06 |

**Giai đoạn kiểm (2026) và tiêu chí:**

| | H-A short Perp | H-A long Spot | H-B short Perp | H-B long Spot |
|---|---|---|---|---|
| Dò: số lệnh / TB mỗi lệnh / t | 108 / +0,85% / 1,76 | 47 / −0,24% / −0,33 | 14 / +1,05% / 1,06 | 11 / −0,69% / −1,18 |
| Kiểm: số lệnh / TB mỗi lệnh | 62 / **−0,10%** | 29 / −1,03% | 9 / −0,29% | 10 / +4,20% |
| Kiểm khi bỏ ZEC từ 1/10 | −0,47% | −1,03% | −0,63% | +4,20% |
| Phí gấp đôi: dò / kiểm | +0,65% / −0,31% | −0,54% / −1,33% | +0,85% / −0,49% | −0,99% / +3,90% |
| Số coin có lãi | 3 / 5 | 2 / 5 | 3 / 5 | 2 / 5 |
| Tương quan theo tháng với Setup-2 | 0,07 | 0,47 | −0,21 | −0,17 |
| Tiêu chí 1–6 | ✗ ✗ ✗ ✓ ✗ ✓ | ✗ ✗ ✗ ✗ ✗ ✓ | ✗ thiếu mẫu ✗ ✓ ✗ ✓ | ✗ thiếu mẫu ✗ ✗ ✓ ✓ |
| **Kết luận** | **Không đạt** | **Không đạt** | **Không đạt** | **Không đạt** |

**Chi tiết của H-A short Perp, chiều có tín hiệu nhất:**
- Theo năm: 2024 +0,37% (43 lệnh), 2025 +1,17% (65 lệnh), 2026 −0,10% (62 lệnh).
- Lợi thế ở giai đoạn dò không còn ở năm 2026. Hai lệnh ZEC tháng 10/2026 có lãi; bỏ chúng đi thì phần kiểm còn −0,47%. Như vậy quy luật Julius thấy trên chart là có thật trong tháng 10, nhưng không đúng cho cả năm.
- Theo cách thoát: chạm stop 107 lệnh (TB −2,74%), chạm chốt lời 49 lệnh (+6,16%), hết thời gian 14 lệnh (+5,45%).
- Danh mục mô tả (rủi ro 1% mỗi lệnh): +21,8%, nhưng sụt giảm tối đa 20,6%.
- Funding ±0,01% mỗi 8 giờ chỉ làm thay đổi khoảng ±0,05% mỗi lệnh.

**H-B có tương quan âm với Setup-2:** danh mục H-B lãi +18,2% trong 14 tháng Setup-2 lỗ. Nhưng hai năm rưỡi chỉ có 44 lệnh, và mỗi chiều không chiều nào dương ở cả hai giai đoạn. Long Spot +4,20% ở giai đoạn kiểm chủ yếu đến từ ZEC: cộng dồn cả hai giai đoạn ZEC được +45,5%, còn NEAR và SOL đều lỗ.

**Chấm các dự đoán:**

| Dự đoán | Kết quả |
|---|---|
| H-B không đạt vì quá ít lệnh | Đúng. Mỗi chiều có 9–10 lệnh ở giai đoạn kiểm, dưới mức 20 |
| H-A short có lãi nhỏ ở giai đoạn dò, khoảng +0,3% đến +0,8%, tỉ lệ thắng trên 45%, có thể chưa đạt t ≥ 2 | Gần đúng về mức lãi (+0,85%) và t (1,76). Sai về tỉ lệ thắng (39%) |
| Chạm kênh trên (Z2) ít lệnh hơn nhưng tốt hơn mỗi lệnh | Sai: ít lệnh hơn nhưng âm (−0,42% và −0,13%) |
| H-A long yếu hơn short | Đúng: cả 6 biến thể long đều âm |
| Tương quan với Setup-2 thấp | Đúng: từ −0,21 tới 0,47 |

**Kết luận:** không chiều nào đạt nên nghiên cứu dừng tại đây, đúng như đã đăng ký: không dò thêm biến thể trên giai đoạn kiểm. Setup-3 không được đề xuất soak.
