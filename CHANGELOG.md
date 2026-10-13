# Changelog

File này ghi lại các thay đổi đáng chú ý của dự án, bắt đầu từ ngày 2026-10-06. Các thay đổi trước đó xem trong `git log` và các file `docs/*-research.md`.

Mỗi mục gồm ngày, tóm tắt, commit liên quan và link sang tài liệu chi tiết. Mục mới nhất nằm trên cùng.

## 2026-10-13 — Ngoại lệ gate cho ZEC Perp và HYPE Perp (phụ lục ADR-007)

Nhánh: `feature/slot-runtime`. Gate theo từng route loại ZEC Perp (−1,7% trong 30 tháng) và HYPE Perp (−4,4% trong 13 tháng); Julius chọn giữ cả 10 route bằng một ngoại lệ có phạm vi.

- **Mã:** `CoinPlan.waive_net_return` (thị trường của coin), `active-set init/switch --waive-net-return COIN:MARKET`, `GateEvidence.net_return_waived`. Gate chỉ bỏ tiêu chí lãi ròng > 0, còn lỗ từ 5% trở lên vẫn bị loại (`NET_RETURN_WAIVER_FLOOR_PCT`); số lệnh, DD < 15%, độ phủ dữ liệu vẫn áp dụng; mỗi miễn trừ để lại `waived_nonpositive_net_return` trong báo cáo.
- **Kiểm thử:** 3 test mới (`tests/test_net_return_waiver.py`).
- **Chạy thử lần cuối với đúng lệnh triển khai:** cả 10 route qua gate, 3 route qua nhờ miễn trừ (ZEC Perp và HYPE Perp ở lãi ròng, HYPE Spot ở số lệnh). HYPE Perp chỉ cách chặn dưới 0,64 điểm.
- **Sửa diễn đạt:** bỏ cách viết "Spot ×5 và Perp ×3" như một cấu hình; cấu hình vẫn là 5 coin, cả hai thị trường.

## 2026-10-13 — Chạy thử quy trình triển khai trên database tạm (ADR-007)

Nhánh: `feature/slot-runtime`. Chạy đúng các lệnh của checklist trên một database tạm với dữ liệu công khai (chỉ đọc), không chạm VPS hay Demo. Kết quả: [checklist](docs/runbooks/slot-runtime-demo-rollout-checklist.md).

- **Lỗi tìm thấy và đã sửa:** backfill Spot HYPE bị từ chối vì nến M15 đầu tiên (11:00) muộn hơn nến H4 đầu (08:00) ba giờ; `setup2.sync` nay dò nến đầu tiên thật của Spot. Có test.
- **CLI:** `active-set init/switch --anchor [COIN=]YYYY-MM-DD` cho phép neo khác nhau theo coin.
- **Gate replay v2 trên dữ liệu thật:** với neo 2025-06-01 cho cả 5 coin, 4 trong 10 route bị loại (ETH Spot −2,6%, NEAR Perp −0,7%, ZEC Perp −2,8%, HYPE Perp −4,4%); với neo 2024-01-01 cho 4 coin cũ, 7 trong 8 route qua, chỉ ZEC Perp bị loại (−1,7%). HYPE Spot qua nhờ miễn trừ (0 lệnh).
- **Điều cần Julius quyết:** neo theo coin, và chấp nhận rằng ZEC Perp và HYPE Perp không kích hoạt được theo quy tắc hiện tại (không có đường ngoại lệ).
- **Quy trình Perp:** phải chạy `replay funding fetch` phủ đến ranh giới H4 gần nhất rồi truyền `--funding-id`, nếu không gate báo `funding_coverage_incomplete`.

## 2026-10-13 — Spot HYPE trên proxy giá Perp, ngưỡng 400 nến, miễn trừ gate lệnh (phụ lục ADR-007)

Nhánh: `feature/slot-runtime`. Julius muốn Spot HYPE vào Demo cùng các coin khác thay vì chờ tới 2027-01-02. Chi tiết và số đo: phụ lục của [ADR-007](docs/decisions/007-slot-rotation-five-coin-runtime.md).

- **Đo trước khi làm (dữ liệu công khai):** từ ≥ 400 nến làm nóng, bộ lọc xu hướng gần như không đổi (EMA200 trong 0,1%); Perp so với Spot HYPE lệch 0,057% trung bình, tối đa 0,125%; ghép giá Perp làm EMA200 lệch 0,02% (trung vị); volume Spot chỉ bằng ~5% Perp nên **volume và volume profile không bao giờ được ghép**.
- **Active set (`CoinPlan`):** thêm `warmup_bars` (400 đến 600, mặc định 600), `spot_price_proxy_until` và `waive_min_trades`; payload cũ giữ nguyên từng byte. `slots_plan()` và `active-set init/switch --warmup COIN=N --spot-proxy COIN=ISO --waive-min-trades COIN:spot`.
- **Chuỗi ghép (`proxied_bars`):** nến H4 Spot trước mốc lấy OHLC từ Perp, volume 0; M15 không ghép. Bộ đánh giá không thể sinh lệnh vào khi cửa sổ volume MA20 chạm nến proxy (`proxy_volume_warmup`) hoặc profile chưa đủ 20 ngày Spot thật (`profile_incomplete`). `sync` chỉ tải Spot từ mốc, trail stop và replay dùng cùng chuỗi.
- **Gate:** `GateEvidence.required_bars`, `min_trades_waived`; cửa sổ replay bắt đầu tại neo + ngưỡng của coin; với miễn trừ, 0 lệnh qua, 1 đến 5 lệnh vẫn bị loại nếu lãi ròng ≤ 0 hoặc DD ≥ 15%; mọi miễn trừ để lại `waived_*` trong số liệu và nhãn `setup2_spot_price_proxied_from_perp`. Miễn trừ tự hết hiệu lực khi Spot đủ ngưỡng nến thật.
- **Kiểm thử:** 11 test mới (`tests/test_spot_proxy.py`); toàn bộ suite 1515 qua, 1 lỗi có sẵn.
- **Đo trên dữ liệu công khai:** bộ đánh giá live trên chuỗi Spot HYPE ghép (2.882 nến proxy + 116 nến Spot) không có lệnh vào Spot; nến mới nhất không qua bộ lọc EMA200, EMA50, DMI, volume (HYPE đang dưới các đường trung bình); Perp short đã kích hoạt 38 lần, lần gần nhất 2026-10-12 20:00Z.
- **Điều cần nói thẳng:** replay của Spot HYPE gần như không có lệnh (volume và profile chỉ có từ 2026-09-24, lệnh vào chỉ có thể từ 2026-10-14 08:00Z), nên route qua gate bằng bằng chứng quy trình (soak 14 ngày, heartbeat, giới hạn rủi ro) chứ không bằng lệnh đóng như các route khác. Gỡ proxy khi đủ 600 nến Spot thật (2027-01-02).

## 2026-10-13 — Runtime slot 1/3 túi, 5 coin (ADR-007)

Nhánh: `feature/slot-runtime`. Julius chốt thiết kế 4 coin, slot 1/3 túi, Donchian 25 và yêu cầu xây hệ thống, thêm HYPE thành 5 coin, triển khai lên Binance Demo, Jev vẫn xác nhận. Quyết định: [ADR-007](docs/decisions/007-slot-rotation-five-coin-runtime.md). **Đã làm xong phần mã và kiểm thử; chưa push, chưa deploy, chưa chạm Demo/VPS.** Checklist triển khai: [slot-runtime-demo-rollout-checklist](docs/runbooks/slot-runtime-demo-rollout-checklist.md).

- **Profile quy tắc `setup2_slots_v1`:** Donchian 25/10 (khoá trong hợp đồng quy tắc), stop chặn 5%, evaluator live nhận `profile`. `setup2_v1` và hash quy tắc cũ không đổi.
- **Tập coin hoạt động:** tối đa 5 coin cho profile mới (3 cho `setup2_v1`), split 50/50, mỗi coin một chế độ; `slots_plan()` và `active-set init --profile setup2_slots_v1 --coin COIN:MODE --anchor YYYY-MM-DD`. HYPE chạy `perp` cho tới khi đủ nến H4 Spot.
- **Phân bổ:** `DemoAllocation.profile = setup2_slots_v1`, `slot_cap()` = 1/3 vốn thực hiện của túi × hệ số ATR, chặn bởi vốn rảnh của túi; stop 5% cho cả Spot lẫn Perp; chính sách cổng bỏ giới hạn net-delta một-parent (cạnh túi 50% và 3 slot đã đủ).
- **Vòng lặp thực thi (`_slots_cycle`):** mỗi nến H4 thu quan sát của mọi route, thoát Donchian trước, xếp hạng ứng viên một lần cho cả danh mục (cổng PnL, giới hạn 3 vị thế, một vị thế mỗi coin, xoay vị thế hơn > 5 điểm ADX), **chỉ đóng vị thế yếu sau khi ứng viên mới đã được luật và Jev xác nhận**; ghi `slot_selection` vào journal.
- **Mã dùng chung với nghiên cứu:** logic chọn lệnh tách thành `replay_v2/donchian_rotation_select.py` (cùng `adx_of`, `volume_ratio`, `trend_strength`); engine nghiên cứu và runtime gọi cùng một hàm.
- **Hồi quy nghiên cứu (cửa sổ 2026):** 18/18 ca `rotation` giống từng byte `result_id` so với commit ngay trước refactor; 8/8 ca `capacity` giống lần chạy trước, cả sau khi tách hàm trợ giúp; ca `rotation-window` chỉ khác ở trường `slot_divisor` thêm vào `variant`.
- **Kiểm thử:** 11 test runtime mới (xếp hạng, cổng PnL, xoay vị thế và Jev loại, tái đầu tư, profile quy tắc), 5 test active set và phân bổ, 5 test profile quy tắc và parity với nghiên cứu cho cửa sổ 25.
- **Stage 0 (5 coin trên 3 vị thế, BTC thay HYPE, 8 ca, đều tái hiện ca 4 coin):** Donchian 25, cả cửa sổ: +96,6% / DD 11,4% / 24 lần xoay vị thế so với +103,3% / 9,8% / 8 lần của 4 coin; lợi nhuận trên DD 8,5 so với 10,5. 2026 không phân biệt được (+22,0% so với +22,3%). Thêm coin thứ năm làm số lần xoay tăng gấp ba và giảm khoảng 2 đơn vị lợi nhuận/DD ở cửa sổ đầy đủ: là một lựa chọn, không phải cải thiện. BTC không phải HYPE.
- **Quyết định về coin thứ năm (2026-10-13):** Julius giữ HYPE sau khi thấy kết quả Stage 0. Demo liệt kê cả 5 coin ở Spot và USD-M Perp (`TRADING`); tín hiệu Spot HYPE chỉ có từ 2027-01-02 (600 nến H4 từ 2026-09-24), nên HYPE chạy `perp` trước rồi chuyển sang `both` bằng `active-set switch`.
- **Còn lại trước khi triển khai (Julius duyệt):** sao lưu và deploy, `active-set init`, soak, cấu hình và kích hoạt Demo.
- **Chưa kiểm chứng:** HYPE (không có lịch sử Spot, không có số liệu nghiên cứu), hành vi thật của Binance Demo với ZEC và HYPE, và chênh lệch live so với replay do Jev và khớp lệnh.

## 2026-10-13 — Giới hạn mỗi túi bằng số coin (đăng ký trước)

Nhánh: `research/rotating-slots`. Nghiên cứu offline; thiết kế và dự đoán nằm trong [rotating-positions-research](docs/rotating-positions-research.md).

- **Lý do:** lần chạy trước cho thấy giới hạn 3 vị thế mỗi túi không bao giờ chạm trần; với 3 coin nâng giới hạn lên bằng số coin chính là ca đã chạy, nên chỉ rổ 4 coin có gì mới (4 mỗi túi, tối đa 8 vị thế).
- **Thêm:** `slot_divisor` (mặc định `max_positions`, ca cũ không đổi), universe `rotation-capacity` (4 thiết kế × chi phí thường/×2) và verdict `evaluate_capacity`. Slot tính bằng 1/`max_positions` nên nâng giới hạn 3 → 4 cũng thu nhỏ vị thế từ 1/3 xuống 1/4 túi; `slot_divisor` tách hai tác động.
- **Kiểm thử:** 3 test mới; 252 test Donchian hiện có vẫn qua.
- **Kết quả (4 coin có ZEC, 16 ca cả hai cửa sổ, đều tái hiện):**
  - Nâng giới hạn lên 4 mỗi túi với slot giữ 1/3 chỉ thêm +1,5 điểm lợi nhuận và +1,1 điểm DD (+90,2% / 10,5% so với +88,7% / 9,4%); 2026 hoà (+22,9% so với +22,8%).
  - Yêu cầu của Julius (4 mỗi túi, slot 1/4): +68,8% / DD 8,2%, thấp hơn đối chứng vì vị thế nhỏ hơn. Chỉ đổi cỡ (3 mỗi túi, slot 1/4): +68,9% / 7,3%, cùng lợi nhuận trên DD với đối chứng (9,50 so với 9,44).
  - Cỡ slot là tham số thực sự; giới hạn bằng số coin biến thiết kế thành trọng số cố định theo coin (không xoay vị thế). Giữ 3 vị thế mỗi túi, slot 1/3.
  - Hai dự đoán trượt: lợi nhuận của `THIRD` (dự +95–115%, ra +90,2%) và `4S` (dự trong ±15%, ra −22%).

## 2026-10-12 — Donchian 25/20 và giới hạn 3 vị thế mỗi túi (đăng ký trước)

Nhánh: `research/rotating-slots`. Chỉ nghiên cứu offline; thiết kế, 30 ca mỗi cửa sổ, đối chứng và dự đoán nằm trong [rotating-positions-research](docs/rotating-positions-research.md).

- **Thêm:** cửa sổ vào Donchian 25 và 20 (thoát giữ 10) và `limit_scope='sleeve'` (tối đa 3 vị thế mỗi túi, một coin được giữ cả Spot lẫn Perp); universe `rotation-window`; verdict `evaluate_window` kèm số vị thế mở đồng thời tối đa.
- **Code:** `OBSERVED_PAIRS` thêm `(25,10)`; `RotationConfig.entry_window` và `limit_scope`; xoay vị thế chỉ trong cùng túi khi giới hạn theo túi. Cấu hình cũ giữ nguyên kết quả.
- **Kiểm thử:** 5 test mới (cửa sổ 25, giới hạn theo túi, 6 vị thế, xoay trong cùng túi, verdict cửa sổ).
- **Kết quả (60 ca, cả cửa sổ và chỉ 2026, đều tái hiện):**
  - Giới hạn 3 vị thế mỗi túi cho kết quả **giống hệt** giới hạn 3 tổng ở mọi ca; số vị thế mở đồng thời tối đa luôn là 3 (không bao giờ 4 hay 6), vì các coin chạy cùng chiều nên tín hiệu bị chặn luôn nằm cùng túi.
  - Cửa sổ vào ngắn hơn không phải cải thiện: 3 coin +73,2% → +73,1% (25) → +68,6% (20); 4 coin có ZEC +88,7% → +103,3% → +94,2%, nhưng đó là do ZEC và NEAR, còn ETH kém dần (Perp ETH +61 → +27 → +2). 2026 không phân biệt được (±1,3 điểm).
  - Số lệnh chỉ tăng 2–5%, thấp hơn dự đoán 15–40%.
  - Kết luận: giữ cửa sổ vào 30; giới hạn theo túi không thêm gì so với 3 vị thế tổng.

## 2026-10-12 — Nghiên cứu xoay vị thế: 2 túi, cổng PnL, stop 5%

Nhánh: `research/rotating-slots`. Chỉ nghiên cứu offline, không đụng runtime hay VPS. Thiết kế đăng ký trước tại [rotating-positions-research](docs/rotating-positions-research.md).

- **Yêu cầu của Julius:** thêm HYPE, 50/50 Spot/Perp hai túi riêng, tái đầu tư, bỏ trọng số cố định theo coin, tối đa 3 vị thế mở, vị thế mới phải đóng một vị thế yếu nhất, chỉ vào lệnh khi sổ lệnh đang lãi, stop 5% mỗi vị thế.
- **Phát hiện khi dựng thử nghiệm:** với 3 coin và một vị thế mỗi coin, giới hạn 3 vị thế không bao giờ chặn được, nên xoay vị thế chỉ có nghĩa với coin thứ 4. HYPE chưa có lịch sử trong dữ liệu đã đóng băng (Perp từ 2025-05-30, Spot từ 2026-09-24) nên dùng ZEC làm coin đại diện thứ 4.
- **Code:** `RotationBook`, `RotationConfig`, nhánh xoay vị thế trong engine, stop cứng trong `ATRTrail` (mặc định không đổi), verdict `donchian_rotation_verdict`, universe `rotation`. 10 test mới; 243 test Donchian liên quan vẫn qua.
- **18 ca**, tiêu chí cơ học và dự đoán đã ghi trước khi chạy. **Kết quả:** không ca nào qua tiêu chí.
  - Thiết kế đúng ý (không giới hạn vốn mỗi vị thế): +152,7% nhưng DD 19,2% và 20 lần dừng lỗ ngày; bản 4 coin +153,5% / DD 21,6% / 28 lần dừng.
  - Bản slot 1/3 túi: +73,2% / DD 9,9% (3 coin) và +88,7% / DD 9,4% (4 coin).
  - Lợi nhuận trên mỗi đơn vị DD gần như bằng nhau (7,1 đến 8,0): phần tăng thêm là do cỡ lệnh, không phải edge mới.
  - Xoay vị thế gần như không hoạt động (6 lần trong 4,75 năm); giới hạn 3 vị thế làm giảm lợi nhuận (+184,7% khi bỏ giới hạn).
  - Stop 5% giúp (+152,7% so với +131,3% khi bỏ), cổng PnL gần như trung tính.
  - Dự đoán sai một điều: `ROT` không thua `FIXED5050` về lợi nhuận mà gần gấp đôi.
- **Chạy lại chỉ cửa sổ 2026 (tài khoản mới 1.000 USDT):** `ROT` +18,0% / DD 11,4% (50/50 cố định +14,2% / 10,3%), `ROT-SLOT` +15,5% / 9,5%, 4 coin `ROT` +34,0% / DD 14,6%, `ROT-SLOT` +22,8% / 9,4%. Ba ca qua tiêu chí cơ học, nhưng đây là một đoạn của cửa sổ đã thấy, chênh lệch nhỏ so với nhiễu (53–82 lệnh), stop 5% từ có ích thành tốn 7–10 điểm, và phần hơn ở 4 coin là của ZEC. Xoay vị thế chỉ hoạt động 1–2 lần.
- **Sự cố:** ổ đĩa đầy 100% làm study dừng ở ca 9; đã chạy `uv cache clean` (Julius duyệt) và `--resume`.

## 2026-10-12 — H1-SLOWREGIME: bộ lọc chế độ chậm tính hoàn toàn trên H1 (đăng ký trước)

Nhánh: `research/h1-slowregime` (từ `research/wide-basket-slots`). Julius hỏi liệu mọi ứng viên H1 đều tính chỉ báo ở H1. Không: `H1-REGIME` lấy EMA200/50 và ADX/DMI14 từ nến H4 (ứng viên lai). Julius chọn thêm bản thuần H1.

- **Ứng viên:** EMA800/EMA200 và ADX/DMI56 trên nến H1 (cùng độ dài thời gian với EMA200/50 và ADX14 của H4); Donchian, volume, profile và stop giữ như H1 baseline. Ngưỡng ADX 20 kế thừa, không tối ưu. Dự đoán ghi trước: ít lệnh hơn và tốt hơn baseline, gần bản lai, dưới H4.
- **Code:** `adx_period` trong `indicator_series` (mặc định 14, ATR và volume MA không đổi), cặp EMA (200,800), `warmup_hours` theo cấu hình, `build(..., warmup_hours)` (các chuỗi khác giữ warmup cố định), cắt dữ liệu theo từng cấu hình trong `run_study`, `decode_bundle(m15_warmup)`, collector `--m15-warmup-hours`, `--only` và `--universe h1-slowregime`.
- **Phạm vi:** chỉ cửa sổ 2026 (manifest 2022 chỉ có 484 giờ M15 trước ngày bắt đầu): rổ ETH-NEAR-SOL 8 ca và rổ 30 coin 6 ca.
- **Kiểm thử:** 6 test mới (suite 1457 pass).
- **Kết quả (commit `852787d`):** rổ ETH-NEAR-SOL 2026 — `H1-SLOWREGIME` −0,73%, DD 4,69%, 60 lệnh, không lệnh nào giữ quá 48 giờ, trượt 4/5 tiêu chí; bản lai `H1-REGIME` +8,12%, H4 +14,23%. Rổ 30 coin — `H1-SLOWREGIME` −6,52%, DD 28,17%, 347 lệnh (bản lai −6,63%, 832 lệnh), vẫn trượt mọi tiêu chí, H4 +23,40%. Dự đoán "tốt hơn baseline, gần bản lai" chỉ đúng ở phần ít lệnh hơn; trên rổ 3 coin nó kém baseline. Kết luận: lợi thế của bản lai không đến từ "chế độ chậm" mà từ nến H4; mọi ca H1 vẫn thoát bằng kênh và stop H1 nên mất đuôi dài.
- **Ghi chú kỹ thuật:** lần chạy đầu lệch dataset checksum của ca H1 vì metadata tái tạo H4 đếm trên lịch sử dài hơn (kết quả không đổi); đã sửa và chạy lại, 6 ca tham chiếu trùng từng byte. Ở rổ rộng, chỉ config payload có thêm hai khoá mặc định nên config checksum khác, còn summary và ba journal trùng từng byte.

## 2026-10-11 — Họ Setup-2 trên rổ 30 coin, 5 slot chung (đăng ký trước)

Nhánh: `research/wide-basket-slots` (từ `research/setup2-h1`). Julius muốn thử các ứng viên trên rổ 30 coin top, tối đa 5 vị thế mở cùng lúc, thêm bước chọn coin. Chi tiết: [wide-basket-slots-research](docs/wide-basket-slots-research.md).

- **Thiết kế:** cửa sổ 2026-01-01 đến cuối dữ liệu mới thu (RAM không đủ cho 2022–2026 với 30 coin); top 30 theo khối lượng USDT-M 90 ngày, có cả Spot lẫn Perp (thiên lệch sống sót được ghi rõ); 5 slot chung, mỗi slot 20% quỹ vốn thực hiện nhân hệ số ATR, Spot long và Perp short đếm chung, mỗi coin một vị thế, không chia 60/40.
- **Chọn coin:** xếp hạng ADX rồi tỷ lệ volume; đối chứng `H4-random` chọn ngẫu nhiên có seed. **Jev không được phát lại** vì trạng thái của Jev dùng sổ lệnh, OI, long/short, sentiment mà Binance không có lịch sử; nhật ký `selection` ghi mọi ứng viên và điểm để ghép câu trả lời shadow về sau.
- **Code:** `SlotBook` (một quỹ vốn, slot cố định), cổng slot trong engine (gom hai thị trường, xếp hạng, chặn `slot_limit`, một coin một slot), `WideSlotConfig`/`wide_cases` (14 ca), `donchian_wide_data` (chọn rổ, thu từng coin nghiêm ngặt, manifest), `--universe wide-slots`, verdict kèm thống kê slot và tập trung coin. Đường mặc định không đổi.
- **Kiểm thử:** 13 test mới; toàn bộ suite pass (1451).
- **Kết quả (commit `bad14c1`, report `donchian-wide-slots-20261011`; dữ liệu 01/01–11/10/2026, thu hôm nay, loại QNT và LSK do lệch H4/M15):** H4 xếp hạng ADX +23,40% nhưng **DD 30,59%** (rổ 3 coin: +14,23%, DD 10,46%), 328 lệnh, 7 lần dừng lỗ ngày, chi phí ×2 còn +9,85%. Chọn ngẫu nhiên (10 seed bổ sung, không đăng ký trước): trung bình +6,2%, độ lệch chuẩn 7,1, từ −3,7% đến +18,3%; xếp hạng ADX cao hơn cả 10 lần rút (khoảng 2,4 độ lệch chuẩn) nhưng DD không đổi (~30–33%). Cả năm biến thể H1 trượt mọi tiêu chí (−32% đến +4%, DD 36–52%). Trên 30 coin, 15 coin dương, 5 coin tốt nhất kiếm +428 trên tổng +234; tài khoản +236 tới 22/05 rồi đi ngang.
- **Kết luận:** H1 đóng. Rổ rộng chưa dùng được vì DD; điều đáng thử tiếp là xếp hạng với slot nhỏ hơn hoặc trần phơi nhiễm để đưa DD vào giới hạn. Jev vẫn chưa được đo, chỉ có nhật ký `selection` để ghép shadow sau.

## 2026-10-11 — Chạy lại họ H1 trên cửa sổ 2026

Nhánh: `research/setup2-h1`. Julius muốn xem riêng dữ liệu từ đầu 2026. Thêm `donchian_adx_study --start` (cắt nến đã đóng băng sau khi giải mã, tài khoản mới 1.000 USDT, các dòng trước ngày bắt đầu chỉ làm nóng chỉ báo) và quy tắc chia nửa cửa sổ ở điểm giữa (2026-05-18) cho cửa sổ ngắn. Dữ liệu dừng ở 2026-10-02 (hết manifest), không phải hôm nay.

- **Kết quả (commit `1c67af1`):** H4 +14,23%, DD 10,46%, 65 lệnh, chi phí ×2 còn +11,38%. `H1-ADX30` +15,50%, DD 9,80%, 140 lệnh, qua cả năm tiêu chí theo công thức. `H1-REGIME` +8,12% nhưng DD thấp nhất (7,36%) và Spot, Perp đều dương. `H1-GEOM` +4,63% với DD 21%; baseline +8,12%; EMA 50/20 +6,84%.
- **Không đổi gì:** ADX30 hơn H4 12,7 USDT trong khi sai số chuẩn của chênh lệch cỡ 150 USDT; nó là một trong năm ứng viên chọn sau khi toàn cửa sổ cho −13%, kiếm tiền bằng lệnh ngắn (chỉ 2 lệnh giữ > 48 giờ, so với 38 của H4), 2,5 lần chi phí. Giữ nguyên ADR-005 (H4).

## 2026-10-11 — Vòng hai H1: bốn ứng viên một khái niệm (đăng ký trước)

Nhánh: `research/setup2-h1`. Sau khi H1 baseline thua, Julius muốn thử EMA 50/20 và thêm các setup H1 khác. Chi tiết, dự đoán và tiêu chí: mục "Second round" trong [setup2-h1-research](docs/setup2-h1-research.md).

- **Chẩn đoán từ nhật ký lệnh:** lãi của Setup-2 nằm ở đuôi dài. H4 có 240 lệnh giữ > 48 giờ (giá gộp +1.968, phí 113) còn 183 lệnh ≤ 48 giờ mất 1.030; H1 chỉ có 36 lệnh sống quá 48 giờ vì stop ATR×3 trên H1 chỉ bằng nửa khoảng giá của H4. Cỡ EMA không phải đòn bẩy chính.
- **Bốn ứng viên, mỗi cái đổi một khái niệm so với H1 baseline:** `H1-GEOM` (stop ATR×6, thoát Donchian 40), `H1-REGIME` (EMA200/50 + ADX20 lấy từ nến H4 đã đóng), `H1-EMA50-20` (câu hỏi của Julius), `H1-ADX30`. Dự đoán ghi trước, năm tiêu chí giữ nguyên.
- **Code:** `ATRTrail(multiple=3)`, `indicator_series`/`entry_filters` nhận cặp EMA (khoá cũ giữ nguyên với cặp mặc định), `stop_atr_multiple`, cặp thoát (30,40), cổng chế độ H4 gate theo nến đã đóng, chuẩn bị đặc trưng lười theo cấu hình, `--universe h1-family`, verdict theo họ kèm hồ sơ thời gian giữ lệnh.
- **Kiểm thử:** 11 test mới; toàn bộ suite pass (một lần `test_compact_ab` fail rồi pass khi chạy lại, không tái hiện được trong 8 lần chạy riêng).
- **Kết quả (commit `9b85a29`, report `donchian-setup2-h1-family-20261011`):** ca H4 và H1 baseline trùng từng byte với các report trước. Không ứng viên nào đạt. `H1-GEOM` tốt nhất: +63,26% (H4 +81,75%), DD 24,70%, 1.205 lệnh, chi phí 703 so với 201 USDT, chi phí ×2 còn −1,57%; số lệnh giữ > 48 giờ tăng từ 36 lên 500. `H1-REGIME` −17,21%, `H1-ADX30` −12,96%, `H1-EMA50-20` −33,70% (đúng dự đoán: EMA nhỏ hơn không giúp, thậm chí tệ hơn). **Kết luận: giữ H4.** Giả thuyết chẩn đoán (stop quá sát cắt đuôi dài) đúng, nhưng H1 vẫn cần gấp ba số lệnh nên chi phí quyết định.

## 2026-10-11 — Nghiên cứu Setup-2 ở khung H1 (đăng ký trước)

Nhánh: `research/setup2-h1` (từ `feature/three-coin-runtime`). Julius hỏi có nên đổi sang khung nhỏ hơn khi dữ liệu ít. Đã trả lời là không đổi Setup-2 vì lý do đó; Julius chọn nghiên cứu H1 như một ứng viên riêng. Chi tiết và tiêu chí: [setup2-h1-research](docs/setup2-h1-research.md).

- **Không đụng gì đang chạy:** ADR-005 và rule hash `bf6fccc9…`, runtime, VPS, pin `replay-v2.2` / `gate-v2.1` giữ nguyên.
- **Ứng viên:** đúng rule Setup-2 nhưng mọi cửa sổ đếm theo nến H1 (Donchian 30/10, EMA200/50, ADX14, volume MA20, profile 120 nến). Bốn ca: H4 đối chứng và H1, mỗi ca ở chi phí thường và gấp đôi.
- **Dữ liệu:** nến H1 gộp chính xác từ M15 đã lưu, không tải thêm, không tạo nến giả. Gộp M15 thành H4 trùng native H4 ở mọi nến Spot đầy đủ; Perp lệch ở bốn cửa sổ đã kiểm toán, ngoài đó thì dừng. Giờ ngừng giao dịch ngày 2023-03-24 vẫn để trống.
- **Tiêu chí ghi trước khi chạy** và do `donchian_h1_verdict` tính: lợi nhuận không thấp hơn H4, DD dưới 15%, chi phí gấp đôi vẫn dương, Spot và Perp đều dương, cả hai nửa cửa sổ (chia tại 2024-10-29) đều dương. Đạt cũng chỉ là căn cứ đề xuất paper-test prospective mới, không bật gì.
- **Code:** `bar_interval` trong engine (mặc định H4 giữ nguyên từng byte, config và `result_id` cũ không đổi), `donchian_hourly.py`, `donchian_h1_verdict.py`, `--universe h1`.
- **Kiểm thử:** 15 test mới; 184 test `donchian_*` và parity Setup-2 vẫn pass.
- **Kết quả (commit `b42b56c`):** ca đối chứng H4 trùng từng byte với kết quả đã công bố (+81,75%, DD 11,58%, 423 lệnh). H1 thua: −26,89%, DD 45,92%, 1.725 lệnh, phí+trượt giá 515 so với 201 USDT; gấp đôi chi phí còn −64,97%. Giá gộp vẫn dương (+250) nhưng bị chi phí nuốt; Spot −93, Perp −176. Cả 5 tiêu chí đều trượt. **Kết luận: giữ H4**, không đề xuất ADR-007.

## 2026-10-10 — Công cụ A/B offline: Jev nhận số hay nhận ký tự

Nhánh: `feature/three-coin-runtime`. Câu hỏi của Julius sau khi đọc bài của alex saint: có nên đổi trạng thái Jev từ số sang ký tự. Chi tiết: [compact-state-ab](docs/compact-state-ab.md).

- **Phát hiện:** hệ thống đã có `compact_v1` (12 token danh mục) và A/B shadow, nhưng **VPS chưa từng chạy một lần gọi compact nào** (115.141 lần numeric, 0 lần compact), vì nhánh shadow nằm ở paper worker còn VPS chạy chế độ soak.
- **Vì sao không chờ A/B trực tiếp:** với Setup-2, Jev chỉ được gọi vài lần mỗi tuần, nên 1.000 cặp sẽ mất hàng chục năm. Chi phí cũng không còn là lý do để đổi.
- **Công cụ mới `intraday/replay_v2/compact_ab.py`:**
  - Chạy lại compact và numeric trên snapshot đã lưu, cùng model, cùng thời điểm, rồi chấm bằng kết quả giá 15 phút đã trưởng thành.
  - Mở source ở chế độ chỉ đọc; cuộc gọi ghi vào scratch store riêng.
  - Có `--dry-run`, `--max-calls`, `--max-usd`.
  - Dùng đúng tiêu chí sẵn có (độ chính xác không kém quá 2pp, Brier không kém quá 0,02, giảm ≥15% token, ≥1.000 cặp), bỏ tiêu chí 14 ngày vì chỉ áp cho lúc thu thập; thêm điều kiện khoảng tin cậy bootstrap.
- **Chưa chạy thật:** cần nạp credit OpenRouter và chạy trong container admin của VPS (có secrets); tốn khoảng $0,2–0,3. Chạy thật chờ Julius duyệt.
- **Kiểm thử:** 7 test mới.

## 2026-10-10 — Runtime 3 coin, đợt C: Perp short theo Setup-2

Nhánh: `feature/three-coin-runtime`. Đã làm xong, chưa deploy.

- **Đổi so với kế hoạch: không nâng schema lên v24.**
  - Rule Perp Setup-2 nằm trong scope `perp_intraday` có sẵn, với `entry_profile=setup2_v1`, giống cách làm cho Spot.
  - Tập coin vẫn kiểm soát qua scope logic `perp_4h`, nên vòng Jev 30 giây vẫn tắt.
  - Không cần rebuild bảng nào, mọi container vẫn đọc v23. Lý do và các đánh đổi ghi trong ADR-006.
- **Gate:** `perp_setup2_entry` chỉ mở short khi rule bắn tín hiệu và Jev trả lời SELL hoặc STRONG_SELL. `rule_allows_answers` cũng yêu cầu SELL. LLM không được chọn profile này.
- **Worker:** job `asset_perp_setup2_<coin>` chạy mỗi 4 giờ, chỉ cho coin đang hoạt động hoặc coin trong watch. Mỗi lần đồng bộ nến Perp, đánh giá một nến, và chỉ gọi Jev khi coin đang hoạt động và setup đạt đủ filter.
- **Replay:** nhánh short có trailing stop tối đa 10% phía trên giá vào, tính funding ở mức nến, bắt buộc Isolated 1x, dừng ở 3% hoặc 15%.
- **Demo:**
  - Short Setup-2 cần Isolated 1x và thoát theo Donchian, không dùng tín hiệu Take Profit của Jev.
  - Trailing stop trên sàn được thay theo thứ tự **đặt stop mới rồi mới hủy stop cũ**, và chỉ được siết lại.
- **Kiểm thử:** 6 test mới. 1415 pass, 1 fail có sẵn từ trước.

## 2026-10-10 — Runtime 3 coin, đợt B: Spot chạy theo Setup-2

Nhánh: `feature/three-coin-runtime`. Đã làm xong, chưa deploy. Theo [ADR-006](docs/decisions/006-three-coin-runtime.md).

- **Evaluator live `intraday/setup2.py`:**
  - Dùng lại nguyên các hàm chỉ báo, Donchian, bộ lọc và volume profile của phần research, cùng `ATRTrail`.
  - Chỉ báo tính trên chuỗi H4 bắt đầu từ một mốc cố định (anchor).
  - Trailing stop tính lại hoàn toàn từ nến đã lưu, giới hạn 10% so với giá vào, nên khởi động lại không làm mất trạng thái.
  - Test parity trên dữ liệu ngẫu nhiên: live trùng research ở mọi chỉ báo, profile, cờ, bộ lọc, hệ số size và mọi lần cập nhật trailing stop, cho cả long lẫn short.
- **Profile rule `setup2_v1`:**
  - Thêm vào `SpotRuleParameters`. Mặc định không ghi ra JSON, nên hash của rule cũ không đổi.
  - Khóa ở 30/10 và ATR14. LLM không được chọn profile này; đường Donchian cũ từ chối chạy rule Setup-2.
- **Dữ liệu và soak:**
  - `setup2_store`: lưu nến Spot/Perp H4/M15 (không cho sửa), mỗi nến H4 có một dòng observation làm heartbeat.
  - Jev chỉ được gọi khi coin đang hoạt động và setup đạt đủ filter.
  - Lệnh `aigt setup2 backfill|evaluate|candidate`.
- **Replay và gate v2:**
  - Engine có nhánh Setup-2: một lượt tính chỉ báo, quy mô 1/3 của phần 60/40, trailing stop giới hạn 10%, dừng ở 3% hoặc 15%.
  - Gate: cần 600 nến từ anchor và DD < 15%; soak cần 14 ngày và heartbeat ≥95%, không cần 6 setup; không so champion khi khác profile.
  - Không đổi pin version.
- **Thực thi Demo:**
  - Tín hiệu Spot đọc từ nến đã lưu.
  - Stop gốc trên sàn là trailing stop, chỉ siết lại. Thiếu dữ liệu thì giữ stop cũ, hoặc stop 10%.
- **Danh sách `watch`:** tối đa 3 coin ứng viên chạy soak theo rule, không gọi Jev và không trade, để có bằng chứng khi đổi coin. Thêm bằng `aigt active-set switch --watch COIN`.
- **Kiểm thử:** 1409 pass, 1 fail có sẵn từ trước.

## 2026-10-10 — Runtime 3 coin, đợt A: tập coin hoạt động và giới hạn theo tỷ lệ chia vốn

Nhánh: `feature/three-coin-runtime`. Quyết định ghi tại [ADR-006](docs/decisions/006-three-coin-runtime.md): xây runtime trước khi paper-test ADR-005 có kết quả. Làm 3 đợt A → B → C, mỗi đợt chỉ deploy khi Julius duyệt riêng.

### Đợt A (đã làm, chưa deploy)

- **Tập coin hoạt động** (`intraday/active_set.py`, `aigt active-set show|init|switch`):
  - Mỗi lần đổi là một version mới, chỉ ghi thêm, có actor và reason. Mỗi coin có chế độ, tỷ trọng Spot/Perp và mốc tính chỉ báo; tỷ lệ chia vốn 60/40.
  - **Chưa có version nào thì hệ thống chạy y như cũ.**
- **Khi đã có version:**
  - Chỉ scope theo rule của 3 coin được gọi Jev.
  - Coin khác, kể cả BTC, vẫn ghi dữ liệu thị trường và ghi `skipped_inactive` cho mỗi slot H4.
  - `perp_intraday` (Jev 30 giây) và `spot_daily` tắt cho mọi coin.
  - Auto-proposal LLM, review độ tin cậy hằng tuần và gate tuần bỏ qua các scope không hoạt động.
- **Điều kiện để đổi tập coin:**
  - Demo phải đang tạm dừng và không còn vị thế; BTC paper cũng phải tạm dừng và không còn vị thế.
  - Coin mới cần ≥600 nến H4 liền mạch và bằng chứng gate Setup-2. Bằng chứng này sẽ có từ đợt B và C, nên hiện tại chưa thêm được coin mới.
- **Giới hạn rủi ro theo tỷ lệ chia vốn:**
  - Cấu hình Demo `profile=setup2_v1` gắn với một version của tập coin; tạo bằng `execution demo configure --from-active-set`.
  - Gross 100%, Perp 40%, margin 40% ở 1x; dừng khi lỗ 3%/ngày hoặc drawdown 15%.
  - Khi tập coin đổi version, Demo tự tạm dừng việc vào lệnh mới; stop bảo vệ vẫn giữ.
  - Cấu hình legacy giữ nguyên giới hạn cũ và JSON cũ.
- **Web và readiness:**
  - Thêm `/api/active-set`; `/api/portfolio` lấy limits từ tập coin; `/api/assets` có trường `active_set`.
  - Readiness có thêm `active_set` cùng các nhãn `history_not_ready` và `no_backtest_evidence`.
- **Kiểm thử:** 16 test mới. 1397 pass, 1 fail có sẵn từ trước (`test_perp_collection_migration`).

## 2026-10-09 — Chọn rổ ETH-NEAR-SOL và đóng băng paper-test mới (ADR-005)

Nhánh: `feature/donchian-basket3`. Julius chọn rổ ETH/NEAR/SOL, chia đều 1/3, áp rule Setup-2. Rổ này xếp thứ 2 trong backtest 10 rổ: +81,75%, DD 11,58%, PF 1,47; khi chi phí gấp đôi vẫn +57,4%.

- **[ADR-005](docs/decisions/005-eth-near-sol-prospective-paper.md):**
  - Hash rule `bf6fccc9…`, trùng với case ETH-NEAR-SOL trong backtest.
  - Mốc đóng băng **2026-10-10 00:00 UTC**.
  - Tiêu chí giữ như ADR-003.
- **ADR-003 (NEAR/SOL/ZEC) dừng trước lần chạy đầu tiên.** ADR-004 ghi nhận rổ ban đầu.
- **`donchian_prospective`:**
  - Chạy rổ ETH-NEAR-SOL bằng `BasketConfig` / `BasketStressConfig`.
  - Thư mục kết quả đổi tên thành `eth-near-sol-prospective-<YYYYMMDDTHHMMZ>{-inputs,}`.
  - Tin Telegram mở đầu bằng `ETH-NEAR-SOL paper:`.
- **Timer:** giữ tên unit `setup2-prospective.*` để không phải xoá file unit cũ, chỉ đổi phần mô tả. Lần chạy đầu vẫn là thứ Hai 12/10 lúc 12:00 giờ Việt Nam.

## 2026-10-09 — Backtest 10 rổ 3 coin với rule Setup-2

Nhánh: `feature/donchian-basket3`. Julius muốn cả hệ thống chỉ chạy 3 coin, giữ rule Setup-2 (A4 + ADX20) và cho Jev xác nhận tín hiệu. Thứ tự là backtest trước rồi mới làm runtime. Chi tiết xem [donchian-basket3](docs/donchian-basket3.md).

### Đã làm

- **Config rổ mới:** `BasketConfig` / `BasketStressConfig` (chi phí x2), áp rule Setup-2 lên 3 coin trong BTC/ETH/NEAR/SOL/ZEC, tỷ trọng chia đều.
- **Chạy runner:** thêm universe `baskets` cho runner ADX, kèm case đối chứng Setup-2.
- **Xếp hạng:** module `donchian_basket_analysis`.
- **Không đổi engine, runtime hay VPS.**

### Kết quả

Dữ liệu 2022-01 → 2026-10, input đóng băng 5 coin.

- **Cả 10 rổ đều có lãi** ở cả hai giai đoạn và cả khi chi phí gấp đôi: lãi +52,8% đến +84,2%, DD 9,9–13,0%, PF 1,35–1,51.
- **Top 3 theo lãi/DD:**
  - BTC-NEAR-SOL: +84,2%, DD 10,6%.
  - ETH-NEAR-SOL: +81,8%, DD 11,6%.
  - ETH-NEAR-ZEC: +66,2%, DD 9,9%.
- **NEAR/SOL/ZEC chia đều xếp thứ 5:** +65,9%, DD 11,3%.
- **5 rổ đứng đầu đều có NEAR.**
- Case đối chứng Setup-2 trùng từng byte với lần kiểm tra ngày 07/10.
- **Lưu ý:** chọn 1 trong 10 rổ trên dữ liệu đã thấy là thiên lệch chọn mẫu, không phải bằng chứng OOS. Nếu đổi khỏi NEAR/SOL/ZEC thì cần ADR mới với mốc đóng băng mới.

### Quyết định vận hành (ADR-004)

Ghi tại [ADR-004](docs/decisions/004-three-coin-system.md), chưa triển khai:
- **Tối đa 3 coin cho toàn hệ thống:** soak, Jev và trade đều chỉ chạy trên 3 coin này.
- **Rule:** Setup-2 tạo tín hiệu, Jev xác nhận.
- **Mỗi coin tùy chỉnh được:** chế độ (Spot, Perp hoặc cả hai) và tỷ trọng riêng.
- **Đổi coin, tỷ trọng hay chế độ:** chỉ khi portfolio đã tạm dừng và không còn vị thế mở.
- **Đổi coin nhanh, không cần backtest:** coin mới cần ≥600 nến H4, soak ≥14 ngày và qua gate v2; được gắn nhãn `no_backtest_evidence`.

### Kiểm thử

- 11 test mới.
- Toàn bộ suite: 1384 pass, 1 fail có sẵn từ trước trên `main` (`test_perp_collection_migration`). Test này pass hôm 07/10 và fail từ 09/10, nhiều khả năng phụ thuộc ngày chạy, không liên quan tới thay đổi này.

## 2026-10-07 — Timer hằng tuần cho paper-test Setup-2

Nhánh: `integrate/donchian-research`. Julius hay tắt máy nên muốn chạy paper-test trên cloud.

- **Không chạy được trên cloud.** Đã thử với routine Claude cloud: sau khi mở mạng cho `api.binance.com` và `fapi.binance.com`, Binance vẫn trả HTTP 451 (restricted location) cho mọi endpoint. GitHub Actions cũng sẽ gặp lỗi tương tự.
- **Chạy trên máy bằng timer.** Mỗi lần chạy, paper-test replay lại toàn bộ cửa sổ từ mốc đóng băng, nên máy tắt hay lỡ một tuần cũng không mất dữ liệu.
- **Lệnh mới `donchian_prospective scheduled`:**
  - Tự chạy `collect` rồi `evaluate` tới cây nến H4 mới nhất, ghi vào `setup2-prospective-<YYYYMMDDTHHMMZ>{-inputs,}`.
  - Bỏ qua nếu mốc đó đã chấm, hoặc nếu chưa có nến H4 nào sau mốc đóng băng.
  - Gặp thư mục dở dang thì dừng, không xoá.
- **Timer systemd user:** thứ Hai lúc 12:00 giờ Việt Nam, `Persistent=true`. Cài bằng `make install-prospective-timer`; file nằm trong `deploy/systemd/setup2-prospective.*` và `scripts/prospective-timer.sh`.
- **Báo qua Telegram:** dùng chung bot của timer paper BTC (`paper-alerts.env`).
  - Mỗi lần chấm mới gửi 1 tin: verdict, số ngày, số lệnh, winrate, PnL (kèm PnL khi chi phí gấp đôi), DD, PF.
  - Chạy lỗi thì gửi lỗi; lần chạy bị bỏ qua thì không gửi gì.
  - Gửi tin thất bại không làm hỏng lần chạy.
- **Gộp vào `main`:** fast-forward `main` lên `integrate/donchian-research`, không có xung đột.
- **Không đổi gì ở rule, hash rule, tiêu chí hay runtime.** Thêm 5 test cho `scheduled` và phần báo tin.

## 2026-10-07 — Gộp nghiên cứu ADX vào engine và đổi paper-test sang Setup-2

Nhánh: `integrate/donchian-research` (dựa trên `feature/a4-prospective-paper`). Julius đổi rổ coin của paper-test sang **NEAR/SOL/ZEC** với ADX20, tỷ trọng NEAR 30 / SOL 40 / ZEC 30. Đây chính là Setup-2 của nghiên cứu ADX.

### Gộp chuỗi nhánh nghiên cứu (`3ecd3c9`)

- Merge `feature/donchian-adx-setups` vào nhánh tích hợp. Nhánh này đã chứa sẵn `donchian-oos-2022-2024`, `donchian-adx-2022-2026` và `donchian-adx-five`.
- Chỉ có 1 xung đột: cách đặt tên phí. Mình thống nhất về `spot_fee`/`spot_slip` vì nhánh cũng dùng chúng cho cost stress; `EquityBook` lấy phí từ config.
- Bỏ qua 2 file đang sửa dở, chưa commit trong worktree `system-trading/donchian-adx`.
- Kiểm chứng:
  - 1370 test pass.
  - 20 case Donchian filter 703 ngày trùng từng byte với lần chạy ngày 06/10.
  - Setup-1…5 trùng toàn bộ summary và journal với kết quả gốc; chỉ `result_id` đổi theo evaluator v1.1.

### Paper-test Setup-2 (ADR-003)

- [ADR-003](docs/decisions/003-setup2-prospective-paper.md) thay thế ADR-002.
  - Hash rule `a9c7140a…`, mốc đóng băng **2026-10-08 00:00 UTC**.
  - Tiêu chí giữ như cũ: ≥ 120 ngày và ≥ 60 lệnh; PF ≥ 1,2; DD ≤ 12%; lãi ròng > 0 ở cả chi phí chuẩn lẫn chi phí gấp đôi.
  - Dự kiến đủ mẫu vào khoảng tháng 6/2027.
- Lịch sử Setup-2, 2022–2026 (dữ liệu đã nhìn trước): +66,40%, DD 10,85%, 419 lệnh, winrate 42,2%, PF 1,40.
- `donchian_prospective.py`:
  - Chạy Setup-2.
  - Chi phí gấp đôi được replay thật bằng `cost_multiplier=2`, không còn là ước tính.
  - `collect` tự tải cả warmup; nếu dữ liệu bị thiếu thì báo lỗi.
  - Cửa sổ không bắt đầu đúng mốc đóng băng chỉ được tính là kiểm tra quy trình (`not_prospective`).
- Đã chạy thử trên dữ liệu thật trước mốc (04/10 → 07/10): tải đủ warmup NEAR/SOL/ZEC, không thiếu nến, mất 9 giây.

## 2026-10-06 — Paper-test prospective cho A4-Donchian30-10

Nhánh: `feature/a4-prospective-paper`. Chọn A4-Donchian30-10 làm ứng viên, vì đây là họ setup duy nhất có cả bằng chứng trong mẫu (703 ngày: +15,9%, DD 8,5%) lẫn ngoài mẫu (2022–24: +34,9%, DD 10,5%; chi phí gấp đôi vẫn +24,9%). Kết luận OOS là Inconclusive, nên **không bật** mà chỉ paper-test trên dữ liệu mới.

### Thay đổi

- **ADR-002 đóng băng rule trước khi thấy dữ liệu mới** (`852849d`): [docs/decisions/002-a4-donchian-prospective-paper.md](docs/decisions/002-a4-donchian-prospective-paper.md).
  - Hash rule `4a8eae77…`, mốc đóng băng 2026-10-02 00:00 UTC.
  - Tiêu chí chốt trước: ≥ 120 ngày **và** ≥ 60 lệnh; profit factor ≥ 1,2; DD ≤ 12%; lãi ròng > 0 cả khi chi phí gấp đôi (ước tính).
- **Module mới `intraday/replay_v2/donchian_prospective.py`:**
  - `collect`: giữ warmup từ bundle đóng băng, chỉ tải dữ liệu công khai sau mốc.
  - `evaluate`: replay đúng engine Donchian filter, kiểm tra replay hai lần, rồi chấm theo tiêu chí.
- **Không đổi gì ở runtime, gate hay lệnh.**

### Kiểm chứng

- Config đóng băng replay trên 703 ngày cho kết quả trùng từng byte (`result_id`, summary, 3 journal) với case A4-Donchian30-10 đã công bố.
- `uv run pytest -q`: 1257 passed (10 test mới, gồm trọn luồng collect → evaluate trên dữ liệu giả).
- Lần chạy đầu: dữ liệu đến 2026-10-06 12:00 UTC (4,5 ngày), 0 lệnh. Có một breakout BTC Spot bị bộ lọc volume profile chặn. Kết luận: `insufficient_sample`.
- Report nằm ở `donchian-prospective-20261006/` và `donchian-prospective-20261006-inputs/`.

### Sửa ngày

Mục dọn code bên dưới trước đây ghi nhầm ngày 2026-10-07; ngày đúng là 2026-10-06. Các thư mục kiểm tra `*-20261007-refactor-check*` vẫn giữ tên cũ, nhưng thực tế cũng được tạo ngày 2026-10-06.

## 2026-10-06 — Dọn code trùng lặp từ code review (không đổi kết quả)

Nhánh: `feature/weekly-gate-automation`. Đây là các mục 5–10 còn lại của `/code-review`. Không tăng VERSION vì output không đổi.

### Thay đổi

- **`journal_hash` dùng chung** (`ef352c4`). Hàm chuyển sang `metrics.py` và thay vòng SHA-256 viết tay ở 3 study (historical, trailing-cadence, mixed portfolio).
- **Decode bundle M15 của Donchian filter một lần** (`e9e293c`). Bản sao frozen chỉ cần khớp fingerprint với input đã kiểm tra. Thời gian chạy giảm từ 4 phút 38 giây xuống 3 phút 48 giây.
- **Mixin `PerpSleeveRisk`** (`45cd323`). `record_perp` và `enforce_perp_risk` trước đây giống hệt nhau ở `IntradayBook` và `ShortReserveBook`, giờ dùng chung một chỗ.
- **Helper EMA50 dùng chung** (`1ef1ac6`). File mới `indicators.py` có `ema50_trend` và `trend_side`.
  - Các nơi dùng: daily filter của portfolio, `daily_directions` (trước tính EMA hai lần), trend H4/H8 và study equity.
  - `donchian_filters` giữ vòng riêng, vì EMA50 ở đó chạy chung vòng với EMA200 và ADX Wilder.
- **`EquityBook` dùng lại `PortfolioBook.close`** (`87b59b3`). Phí và slippage đọc qua `fee_rate`/`slip_rate`; các trường journal của equity giữ nguyên. `EquityBook.enter` vẫn riêng vì logic khác.

### Không làm

- **Mục 10 (ghi Perp curve hai lần):** không đổi.
  - Hai dòng `parent_checkpoint` và `perp_risk` không trùng nhau, vì dòng sau ghi trạng thái lock *sau* bước đánh giá.
  - Bỏ đi một dòng sẽ mất thông tin và làm đổi sidecar đã công bố.
  - Thay vào đó, code có thêm comment giải thích hai stage.

### Kiểm chứng

- `uv run pytest -q`: 1247 passed, gồm 2 test mới cho EMA50, so với thuật toán cũ.
- Chạy lại offline 8 study vào các thư mục `*-20261007-refactor-check*`: equity, historical baseline 24 tháng, trailing-cadence 703 ngày, short-reserve, intraday-timeframes, hold-margin, single-sleeve, Donchian filter.
- Kết quả: 46 `result_id`, 46 summary, 123 journal và 28 sidecar đều **trùng từng byte** với report ngày 2026-10-06. Riêng equity được so với bản gốc ngày 2026-10-03.
- Study portfolio-research không có CLI chạy lại offline. Đường code `daily_trend` của nó đã được kiểm tra qua historical baseline và test.

## 2026-10-06 — Sửa ledger lỗ ngày và chạy lại các study

Nhánh: `feature/weekly-gate-automation`. Nguồn: 4 lỗi logic do `/code-review` tìm ra trong ledger backtest dùng chung (`intraday/replay_v2/`).

### Sửa lỗi

- **Resume sau daily halt không xóa lỗ của ngày mới** (`5b4c4d2`). Trước đây `MixedBook.maybe_resume` reset `day_start` theo equity hiện tại. Khoản lỗ phát sinh đầu ngày UTC mới, trước khi book về flat, vì thế bị xóa. Giờ nếu lỗ ngày mới đã ≥ 3% thì book tiếp tục halt tới 0h hôm sau. `FilterBook` dùng chung logic này.
- **Warmup `mark15m` tải giá mark** (`1ee1273`). Trước đây prefix warmup luôn tải giá trade. `extend()` giờ cũng từ chối ghép prefix khác loại giá, symbol hoặc interval với snapshot cha.
- **Khôi phục kiểm tra bảo toàn cash** (`844f783`). `MixedBook.enter_batch` raise lỗi khi cash xuống dưới `-1e-18`, cùng ngưỡng sai số mà bước đối soát journal đang dùng.
- **Mốc lỗ ngày neo theo giá mark lúc 0h ở mọi engine** (`e0b851d`). Áp dụng cho cả portfolio lẫn sleeve Perp, trong các engine historical, intraday và short-reserve. Trước đây chỉ Donchian filter làm đúng, nên ngưỡng 3%/ngày bị đo trên các mốc khác nhau giữa những study được đem ra so sánh.

### Version evaluator

Commit `29865ec`:

- `historical-mixed-quant` lên v1.3.
- Các evaluator lên v1.1: các hằng phụ trong `historical_mixed.py`, intraday-timeframes, short-reserve, hold-margin, single-sleeve, Donchian filter.
- `mixed-portfolio-research` lên v2.

### Chạy lại 13 study

Commit `de1bdcf`. Mọi study chạy offline từ input frozen (SHA256 giống hệt), và mọi biến thể pass kiểm tra replay hai lần.

- **Không đổi về kinh tế:** Donchian filter (20 ca), short-reserve, hold-margin, intraday A/C, Spot single-sleeve, các preset 24 tháng, trailing-cadence 703 ngày. Chỉ các chỉ số mô tả "ngày Perp tệ nhất" và "giveback" lệch dưới 0,005 điểm %.
- **Thay đổi nhẹ:** lợi nhuận ngày của Perp ở hai biến thể này nằm sát ngưỡng −3%, nên mốc 0h mới làm điểm dừng lỗ dịch một tick 15 phút.
  - Intraday B: +28,16% → +28,10%.
  - Perp-only single-sleeve: −50,13% → −50,57%, số trade 770 → 772.
- **Không chạy lại:** study recorded-Jev có 0 trade nên không bị ảnh hưởng.

Report mới nằm trong `~/.local/state/aigorithmic-trading/reports/*-20261006-rerun*`. Bằng chứng cũ giữ nguyên.

Chi tiết: [perp-intraday-timeframes](docs/perp-intraday-timeframes-research.md), [single-sleeve](docs/single-sleeve-research.md), [perp-short-reserve](docs/perp-short-reserve-research.md), [hold-margin](docs/hold-margin-research.md), [donchian-filter](docs/donchian-filter-research.md), [mixed-portfolio](docs/mixed-portfolio-research.md). Mỗi file có mục "Rerun after daily-loss fixes — 2026-10-06".

### Kiểm thử

- `uv run pytest -q`: 1245 passed.
- Có 5 regression test mới, mỗi bản sửa đều có test riêng.

### Còn mở

Các mục 5–10 của code review (dọn code trùng lặp, cải thiện hiệu năng) để làm ở PR sau.
