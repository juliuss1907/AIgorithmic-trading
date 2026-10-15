# Jev và tín hiệu Setup-2: cổng xác nhận chặn mọi lệnh (2026-10-15)

Trạng thái: **ghi nhận, chờ Julius chọn hướng trước G5.** Chỉ đọc code và database trên VPS; không sửa gì, không deploy.

## Tóm tắt

Với cổng xác nhận hiện tại (`intraday/scoped_gate.py`), Jev sẽ không xác nhận lệnh Setup-2 nào. Nếu giữ nguyên, khi lên Binance Demo
(G5/G6) runtime slot gần như chắc chắn **không vào lệnh nào**. Nguyên nhân là cấu trúc, không phải ngưỡng hơi cao: Jev được hỏi một câu
chung chung trên snapshot 1h, và nó trả lời như một mô hình đảo chiều, ngược hướng với tín hiệu phá kênh.

## Bằng chứng

**Ba tín hiệu soak đầu tiên** (nến H4 đóng 2026-10-15 04:00Z, cả ba là Perp short, luật đạt đủ bộ lọc):

| Route | Jev chọn (xác suất) | Regime | Risk | Toxic | Điều kiện trượt |
|---|---|---|---|---|---|
| ETH Perp | Strong Buy (0,49) | Trending Down 0,99 | High | 0,29 | hướng, độ tin, risk |
| HYPE Perp | Buy (0,37) | Trending Down 0,98 | Medium | 0,16 | hướng, độ tin, chất lượng vào (1,77 + 1 < 3) |
| ZEC Perp | Strong Buy (0,42) | Trending Down 0,97 | High | 0,32 | hướng, độ tin, risk, toxic > 0,30 |

Snapshot Jev nhận lúc đó: RSI14 = 17,5 (ETH), 35,2 (HYPE), 19,5 (ZEC); giá ETH dưới dải Bollinger dưới.

**115.164 câu trả lời Jev `perp_intraday` trên VPS** (BTC, ETH, SOL, NEAR, ZEC, HYPE; 2026-09-25 đến 2026-10-15; chu kỳ 30 giây cũ, cùng
câu hỏi và cùng kiểu snapshot mà Setup-2 Perp dùng):

- Xác suất của lựa chọn cao nhất **chưa lần nào đạt 0,85**; đạt 0,69 (sàn hợp đồng của `PerpRuleParameters`) 929 lần (0,8%), không lần nào là SELL/STRONG SELL.
- Mô phỏng đủ cổng `perp_setup2_entry` (SELL/STRONG SELL, xác suất ≥ ngưỡng, chất lượng ≥ 3, toxic ≤ 0,30, regime Trending Down/Sideways,
  risk không High/Critical): **0 lần qua ở 0,85 và ở 0,69**; 17 lần ở 0,5; 235 lần nếu bỏ hẳn ngưỡng xác suất.
- Khi RSI14 < 30 (vùng mà short phá đáy 25 nến H4 gần như luôn rơi vào): 12.070 câu, Jev chọn BUY/STRONG BUY **11.993 lần (99,4%)**,
  SELL 77 lần; **0 lần qua cổng ở mọi ngưỡng**; risk High/Critical 78%.
- Khi RSI14 > 70: 12.254 câu, BUY chỉ 5 lần. Spot long phá đỉnh rơi vào vùng này, nên cổng Spot (BUY/STRONG BUY, ≥ 0,85, risk không High)
  nhiều khả năng cũng không qua. Câu hỏi `spot_4h` mới có 1 câu trả lời thật, nên điểm này là suy ra từ câu Perp, chưa đo trực tiếp.

## Vì sao

- **Jev không biết luật vừa ra tín hiệu.** Nó nhận snapshot chung (`JevDecisionProvider._state`: RSI14, Bollinger, MACD, order book, funding,
  OI, long/short ratio, giá 1h) và câu hỏi "Choose the appropriate ETHUSDT perpetual trading action now." Không có Donchian, EMA200/50, ADX,
  volume H4 hay hướng mà luật đề xuất. ADR-006 đã ghi điều này ("Jev confirms the short with the existing Perp intraday question and 1 h snapshot").
- **Jev trả lời theo kiểu đảo chiều:** quá bán thì mua, quá mua thì bán. Tín hiệu phá kênh theo xu hướng theo định nghĩa xuất hiện khi RSI ở cực trị,
  nên Jev phản đối gần như mọi lần.
- **Ngưỡng 0,85 trên một phân phối 6 lựa chọn** (Sell và Strong Sell tách riêng) chưa từng đạt được trong lịch sử.
- ADR-007 viết "Jev loại bớt lệnh". Thực tế là loại hết. Replay gate không mô phỏng Jev (`setup2_jev_filter_not_replayed`), nên kết quả G4
  là kết quả của luật không có Jev.

## Ảnh hưởng tới soak và G5

- **Soak 14 ngày không bị ảnh hưởng.** `evaluate_gate_soak` của Setup-2 chạy lại replay gate và đếm heartbeat theo `setup2_observations`;
  câu trả lời của Jev không nằm trong tiêu chí. Mốc 2026-10-28 giữ nguyên.
- **Đổi tham số rule sẽ buộc soak lại:** `evaluate_gate_soak` từ chối khi `rule.content_hash` khác hash của campaign. Mọi thay đổi cách dùng Jev
  nên nằm ở runtime hoặc active set, không nằm trong `PerpRuleParameters`/`SpotRuleParameters`.

## Lựa chọn

| | Hướng | Hệ quả |
|---|---|---|
| **A (đề xuất)** | Với profile `setup2_slots_v1`, Jev chỉ **tư vấn**: luật quyết định vào lệnh; câu trả lời của Jev vẫn được gọi, ghi lại và gửi Telegram để đo sau | Demo chạy đúng thứ đã qua replay gate. Mất một lớp lọc, nhưng lớp này chưa từng được đo là có ích. Cần sửa `scoped_gate`/`multi_runtime`, có cờ audit trong active set (không đổi rule hash), test, phụ lục ADR-007, deploy (duyệt riêng) |
| B | Jev chỉ phủ quyết khi phản đối rất mạnh (ví dụ P(Buy + Strong Buy) ≥ 0,6 cho short, hoặc risk Critical) | Khi RSI14 < 30 chỉ 1,3% câu trả lời qua được, nên thực chất vẫn chặn hầu hết. Không đề xuất |
| C | Hỏi Jev đúng câu: gửi kèm bối cảnh Setup-2 (H4, kênh, EMA, ADX, volume, hướng luật đề xuất) và hỏi có lý do phủ quyết không | Cần thiết kế câu hỏi và chạy song song (shadow) đủ lâu để đo trước khi cho quyền chặn. Có thể làm sau A, như một thí nghiệm |
| D | Giữ nguyên | Demo chỉ kiểm được đường kết nối, không có lệnh nào. Hạ ngưỡng xuống 0,69 không thay đổi gì (0 lần qua) |

Đề xuất: **A trước G5** (2026-10-28), C để sau nếu Julius muốn Jev có vai trò thật.

## Cách tái lập

Truy vấn chỉ đọc trong container `worker` trên VPS (`docker compose ... exec -T worker python -`), database
`/app/state/intraday/intraday.sqlite3` mở `mode=ro`: bảng `signals` (cột `jev_answers`, `state_snapshot`, `scope = 'perp_intraday'`),
áp lại điều kiện của `ScopedEntryGate.perp_setup2_entry` với từng ngưỡng.
