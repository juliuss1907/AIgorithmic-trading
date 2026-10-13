# Checklist: đưa runtime slot 1/3 túi, 5 coin lên Binance Demo (ADR-007)

Trạng thái: **bản nháp chờ Julius duyệt, chưa thực hiện bước nào trên VPS hay Demo.**
Cơ sở: [ADR-007](../decisions/007-slot-rotation-five-coin-runtime.md), [ADR-006](../decisions/006-three-coin-runtime.md),
[checklist 3 coin](three-coin-demo-rollout-checklist.md) (chỉ có trên nhánh `feature/three-coin-runtime`),
[Demo runbook](../binance-demo-execution-runbook.md). Lệnh dưới đây đã đối chiếu với CLI của nhánh `feature/slot-runtime`.

## 0. Cấu hình sẽ chạy

| Mục | Giá trị |
|---|---|
| Profile quy tắc | `setup2_slots_v1`: Donchian **25/10** H4, EMA200/50, ADX14 > 20 và đang tăng, volume ×1,2, volume profile, ATR14×3, **stop chặn 5%** |
| Coin | ETH, NEAR, SOL, ZEC và HYPE, tất cả `both`. HYPE Spot chạy trên **proxy giá Perp** trước 2026-09-24 (ngưỡng làm nóng 400 nến, miễn trừ gate ≥ 6 lệnh), xem phụ lục ADR-007 |
| Túi | Spot 50% / Perp short 1x 50%, tách riêng; vị thế tối đa **1/3 vốn thực hiện của túi** nhân hệ số ATR |
| Danh mục | Tối đa **3 vị thế** trên hai túi, một vị thế mỗi coin; cổng PnL sổ đang mở; xoay vị thế khi ADX hơn > 5 điểm |
| Jev | Luật tạo tín hiệu, Jev xác nhận (short cần SELL/STRONG_SELL); xoay vị thế chỉ đóng vị thế yếu **sau** khi ứng viên được xác nhận |
| Rủi ro | Dừng lỗ ngày 3%, DD 15%, Perp 1x isolated |
| Vốn Demo | 1.000 USDT (mặc định nghiên cứu) |

## 1. Hệ quả cần chấp nhận trước

- [ ] **5 coin và HYPE chưa được đo.** Stage 0 (`rotation-five`, BTC thay HYPE) kiểm tra cơ chế 5 coin trên 3 vị thế; kết quả phải được ghi vào
      `docs/rotating-positions-research.md` **trước G3**, dù là "qua" hay không.
- [ ] Kết quả Demo không bằng replay: Jev loại bớt lệnh, khớp lệnh thật có trượt giá, xoay vị thế chờ xác nhận.
- [ ] Nửa vốn nằm ở túi Perp, chỉ hoạt động khi có tín hiệu short; có thể nhàn rỗi nhiều tuần.
- [ ] `active-set init`/`switch` đổi hành vi thật: tắt soak Jev cũ của các coin ngoài danh sách (bằng chứng được giữ).
- [ ] **Neo chỉ báo (anchor) quyết định thời gian chờ replay gate.** Cửa sổ replay v2 bắt đầu tại `anchor + 600 nến H4` và cần ≥ 6 lệnh đóng cho từng
      route. Với neo mặc định 2026-07-02 mốc này rơi vào 2026-10-10 và đủ lệnh khoảng tháng 3/2027. Với `--anchor 2025-06-01` cửa sổ bắt đầu
      2025-09-09 nên đã có lịch sử để đủ 6 lệnh. Đổi lại chỉ báo bắt đầu từ chuỗi sớm hơn (EMA/ADX hội tụ sau hàng trăm nến).
      Spot HYPE chỉ có từ 2026-09-24: lịch sử trước đó là giá Perp (proxy), ngưỡng làm nóng 400 nến.

## Các cổng duyệt (mỗi cổng cần Julius đồng ý riêng)

| Cổng | Hành động |
|---|---|
| G1 | Push nhánh `feature/slot-runtime` |
| G2 | Sao lưu đã kiểm và deploy mã (worker, web, admin; **không** đụng `demo-execution`) |
| G3 | `active-set init`/`switch` sang 5 coin, profile `setup2_slots_v1` (đổi hành vi) |
| G4 | Bắt đầu soak các route (tiêu credit Jev) |
| G5 | `execution demo configure` và `activate` từng route |
| G6 | Khởi động container `demo-execution` |

## Giai đoạn 1: chuẩn bị, chưa chạm VPS

- [ ] `uv run pytest -q` trên đúng commit; chấp nhận duy nhất lỗi có sẵn `test_perp_collection_migration` (phụ thuộc ngày).
- [ ] Ghi hash commit sẽ deploy: `________`. Bộ tham chiếu nghiên cứu cho `result_id` không đổi (ghi ở CHANGELOG).
- [ ] Credit OpenRouter nạp đủ; `aigt provider list` ổn.
- [ ] Demo key (quyền Spot và USD-M Futures, không rút): `! uv run aigt connect bnb demo` (nhập ẩn). Không dán key vào chat hay `.env`.
- [ ] Binance Demo: One-way, Single-asset, **Isolated**; tài khoản chuyên dụng, không giao dịch tay.
- [x] **Demo liệt kê cả 5 coin ở Spot và USD-M Perp** (`exchangeInfo` công khai của Demo, `TRADING`, kiểm ngày 2026-10-13). Vẫn chạy
      `aigt assets scan COIN --market spot|perp --notional 1000` cho từng route để có thanh khoản, bước giá và lệnh tối thiểu thật.
- [ ] **HYPE Spot chạy trên proxy giá Perp** (Julius chốt 2026-10-13): nến H4 Spot trước 2026-09-24T08:00Z lấy giá từ Perp (volume 0),
      ngưỡng làm nóng 400 nến, miễn trừ gate ≥ 6 lệnh. Volume và volume profile là của Spot thật nên **không có lệnh vào Spot HYPE trước
      2026-10-14 08:00Z** (đủ 20 ngày M15 Spot); báo cáo gate mang nhãn `setup2_spot_price_proxied_from_perp` và `waived_minimum_6_closed_trades`.
      Gỡ proxy và miễn trừ bằng `active-set switch` khi Spot HYPE đủ 600 nến thật (2027-01-02); miễn trừ tự hết hiệu lực khi đó.

## Giai đoạn 2: sao lưu và deploy (G1, G2)

- [ ] `aigt backup create`, rồi `aigt backup verify <file>`; copy `.sqlite3` và `.manifest.json` sang nơi khác.
- [ ] Ghi lại: commit và image ID đang chạy, chế độ worker, rule hash và campaign ID đang active, vị thế mở, trạng thái Demo.
- [ ] Xác nhận **flat**: không vị thế BTC paper, không `perp_intraday`, Demo paused và flat (hoặc chưa từng cấu hình).
- [ ] Julius duyệt push (G1) và deploy (G2). Chỉ rebuild `worker`, `web` (và `admin` khi cần). Không đổi schema (vẫn v23).
- [ ] Sau deploy: `aigt doctor`, `aigt status` ổn; `aigt active-set show` báo chưa có version nếu chưa init; soak cũ vẫn chạy.

## Giai đoạn 3: kích hoạt tập coin (G3)

Điều kiện: Demo paused và flat, BTC paper paused và flat, `perp_intraday` flat, và Stage 0 đã ghi nhận.

```bash
aigt active-set init --profile setup2_slots_v1 --anchor 2024-01-01 --anchor HYPE=2025-06-01 \
  --coin ETH:both --coin NEAR:both --coin SOL:both --coin ZEC:both --coin HYPE:both \
  --warmup HYPE=400 --spot-proxy HYPE=2026-09-24T08:00:00Z --waive-min-trades HYPE:spot \
  --waive-net-return ZEC:perp --waive-net-return HYPE:perp \
  --actor julius --reason "ADR-007 rollout" [--no-demo-portfolio]
```

- [x] **Neo, Julius chốt 2026-10-13** (xem "Chạy thử trên database tạm"): `2024-01-01` cho ETH/NEAR/SOL/ZEC (30 tháng replay, 7 trong 8 route qua gate)
      và `2025-06-01` cho HYPE (Perp chỉ có từ 2025-05-30). Neo chỉ chọn đoạn lịch sử mà gate dùng để chấm từng route; không đổi tín hiệu giao dịch
      sau khi chỉ báo đã hội tụ. Neo `2025-06-01` cho cả năm coin chỉ cho 6 trong 10 route qua gate.
- [ ] `aigt active-set show`: version 1, `rule_profile: setup2_slots_v1`, split 0,5/0,5, 5 coin, HYPE `both` kèm `warmup_bars: 400`, `spot_price_proxy_until` và `waive_min_trades: ["spot"]`.
- [ ] Vòng Jev 30 giây tắt; coin ngoài danh sách ghi `skipped_inactive`.

## Giai đoạn 4: dữ liệu, rule, gate v2 (G4)

Lặp cho từng route (ETH, NEAR, SOL, ZEC, HYPE: spot và perp), 10 route:

- [ ] `aigt setup2 backfill --symbol COIN --market MARKET` (cần 600 nến H4 liền mạch từ neo, 400 với HYPE). Với HYPE backfill **Perp trước, Spot sau**:
      Spot chỉ tải từ 2026-09-24, và nến Perp trước mốc đó phải liền mạch từ neo.
- [ ] `aigt setup2 evaluate --symbol COIN --market MARKET --profile setup2_slots_v1` (chỉ đọc).
- [ ] `aigt setup2 candidate --symbol COIN --market MARKET --profile setup2_slots_v1 --actor julius --reason "ADR-007" [--retire-open]`
      (ghi `rule_id`; `--profile` mặc định là `setup2_v1`, **bắt buộc truyền** cho thiết kế này).
- [ ] Route Perp cần funding phủ cả cửa sổ replay: `aigt replay funding fetch COIN --from <neo + ngưỡng làm nóng> --to <ranh giới H4 gần nhất> --report-dir ...`
      rồi truyền `--funding-id`. Cửa sổ funding kết thúc trước ranh giới H4 cuối thì gate báo `funding_coverage_incomplete`.
- [ ] `aigt assets rules replay CANDIDATE_ID --engine v2`: cần ≥ 6 lệnh đóng (miễn trừ cho Spot HYPE), lãi ròng > 0, DD < 15%, ≥ 600 nến từ neo (400 với HYPE), phủ nến ≥ 99%.
      Chưa đủ lệnh thì `deferred`, chạy lại hằng tuần.
- [ ] Julius duyệt G4, rồi `aigt assets rules start-soak CANDIDATE_ID [--evaluation-id REPLAY_EVALUATION_ID]`; soak ≥ 14 ngày, heartbeat ≥ 95%.
- [ ] `aigt assets rules evaluate CANDIDATE_ID`, rồi `aigt assets rules activate CANDIDATE_ID --evaluation-id SOAK_EVALUATION_ID`.

## Giai đoạn 5: cấu hình và kích hoạt Demo (G5, G6)

- [ ] Leverage **1x** cho từng cặp Perp (`aigt perp COIN -leverage`, nhập 1); chọn venue Demo cho từng route
      (`aigt assets venue set COIN --market spot|perp --venue bnb --environment demo`).
- [ ] `aigt execution demo configure --from-active-set --capital 1000 --source-database state/intraday/intraday.sqlite3`
      (cấu hình ở trạng thái paused; profile và stop 5% lấy từ active set). Kiểm: gross 100%, Perp 50%, margin 50%, dừng lỗ ngày 3%, DD 15%.
- [ ] Preflight từng route: `aigt execution demo preflight --multi --symbol COIN --market MARKET --source-database ...`
      (`target_notional_cap` = 1/3 của 500 = 166,67 USDT lúc đầu).
- [ ] Julius duyệt G5; kích hoạt từng route bằng soak evaluation của nó:
      `aigt execution demo activate --multi --symbol COIN --market MARKET --source-database ... --evaluation-id SOAK_EVALUATION_ID`.
      Kích hoạt chỉ khớp được nếu champion của route có profile `setup2_slots_v1`; kích hoạt không gửi lệnh.
- [ ] Julius duyệt G6; khởi động `demo-execution` (profile `demo`, `--no-deps`, không kèm `worker`). Chạy `aigt execution demo run --multi --once` trước nếu muốn một vòng thử.

## Giai đoạn 6: theo dõi và đối chiếu sau triển khai

- [ ] Mỗi chu kỳ: `p["slot_selection"]` (số vị thế mở, PnL sổ, từng bước `free`/`rotate`/`blocked` và lý do) có trong portfolio journal.
- [ ] **Đối chiếu parity thật:** so `setup2_observations` của runtime (đặc trưng mỗi nến H4) với replay nghiên cứu cùng cửa sổ: ADX, ATR, cờ vào/thoát phải trùng.
- [ ] Stop gốc trên sàn là bên gần hơn trong ATR×3 và 5%, chỉ siết; đóng khi `donchian_exit` hoặc `rotation_exit`.
- [ ] Điều kiện dừng: `portfolio_loss_limit`, lệch tiền (cash drift), bảo vệ stop thất bại, hoặc `active_set_changed_reconfigure`.

## Lùi lại

Pause portfolio (stop gốc được giữ), `aigt execution demo flatten`, rồi `active-set switch` về version trước (khi paused và flat). Không xoá dữ liệu.

## Chưa kiểm chứng

- Acceptance có giám sát với tiền Demo cho đường lệnh, stop và đối soát (chưa từng có), cũng như ghi và đọc lại leverage.
- Hành vi thật của Demo với HYPE, ZEC (thanh khoản, bước giá, lệnh tối thiểu).
- Hiệu ứng của Jev lên tập lệnh so với replay; xoay vị thế chạy tuần tự (đóng rồi mở) nên có trượt giá giữa hai lệnh.

## Chạy thử trên database tạm (2026-10-13, dữ liệu công khai, chỉ đọc)

Mình chạy đúng các lệnh ở trên (`active-set init`, `setup2 backfill`, `evaluate`, `candidate`, `replay funding fetch`, `assets rules replay --engine v2`)
trên một database tạm, không chạm VPS hay Demo.

**Lỗi tìm thấy và đã sửa:** nến M15 Spot HYPE đầu tiên là 11:00, ba giờ sau nến H4 đầu (08:00); backfill từ đúng mốc proxy bị từ chối
(`incomplete native Futures window`). `setup2.sync` nay dò nến đầu tiên có thật của thị trường Spot trước khi tải (có test).

**Kết quả gate replay v2 theo route** (cửa sổ bắt đầu tại neo + ngưỡng làm nóng, kết thúc 2026-10-13 12:00Z, một vị thế một coin, slot 1/3 túi 50%):

| Route | Neo 2025-06-01 (13 tháng) | Neo 2024-01-01 (30 tháng) |
|---|---|---|
| ETH Spot | **loại**: −2,63% / 14 lệnh | qua: +1,98% / 36 lệnh |
| ETH Perp | qua: +4,82% / 20 lệnh | qua: +5,44% / 46 lệnh |
| NEAR Spot | qua: +9,54% / 18 lệnh | qua: +9,38% / 40 lệnh |
| NEAR Perp | **loại**: −0,70% / 14 lệnh | qua: +5,03% / 39 lệnh |
| SOL Spot | qua: +0,06% / 15 lệnh | qua: +1,53% / 39 lệnh |
| SOL Perp | qua: +4,11% / 17 lệnh | qua: +4,54% / 42 lệnh |
| ZEC Spot | qua: +14,03% / 24 lệnh | qua: +15,09% / 47 lệnh |
| ZEC Perp | **loại**: −2,79% / 11 lệnh | **loại**: −1,69% / 31 lệnh |
| HYPE Spot | qua (miễn trừ): 0 lệnh | không có lịch sử |
| HYPE Perp | **loại**: −4,36% / 20 lệnh, DD 7,1% | không có lịch sử |

Điều cần quyết:

- **Gate là kiểm tra từng route độc lập, lãi ròng > 0 trong cửa sổ**; kết quả nhạy theo cửa sổ. Cửa sổ dài cho nhiều lệnh hơn (31 đến 47 so với 11 đến 24), nên đáng tin hơn
  ngưỡng "≥ 6 lệnh". Chọn neo theo coin là quyết định của Julius, không phải để "cho qua gate".
- **ZEC Perp bị loại ở cả hai cửa sổ và HYPE Perp bị loại ở cửa sổ duy nhất có thể** theo quy tắc gate từng route. Julius chọn **ngoại lệ có phạm vi**
  cho đúng hai route này (`--waive-net-return`): chỉ bỏ tiêu chí lãi ròng > 0, chặn dưới −5%, các tiêu chí khác giữ nguyên. Cấu hình vẫn là 5 coin, cả Spot
  lẫn Perp (10 route).
- **ETH Spot yếu trong 13 tháng gần nhất** (−2,6%) dù dương trong 30 tháng; HYPE Perp là coin đã lỗ trong cửa sổ có thể kiểm.

### Chạy thử lần cuối với đúng lệnh triển khai (2026-10-13)

Neo 2024-01-01 (HYPE 2025-06-01), proxy giá HYPE, ngưỡng 400, miễn trừ số lệnh cho HYPE Spot và miễn trừ lãi ròng cho ZEC Perp, HYPE Perp:

| Route | Gate | Lệnh | Lãi ròng | DD | Miễn trừ |
|---|---|---:|---:|---:|---|
| ETH Perp / Spot | qua / qua | 46 / 36 | +5,44% / +1,98% | 5,53% / 5,26% | không |
| NEAR Perp / Spot | qua / qua | 39 / 40 | +5,03% / +9,38% | 4,93% / 4,94% | không |
| SOL Perp / Spot | qua / qua | 42 / 39 | +4,54% / +1,53% | 3,71% / 5,92% | không |
| ZEC Perp / Spot | qua / qua | 31 / 47 | −1,69% / +15,09% | 5,73% / 4,54% | Perp: lãi ròng |
| HYPE Perp / Spot | qua / qua | 20 / 0 | −4,36% / 0 | 7,11% / 0 | Perp: lãi ròng; Spot: số lệnh |

**HYPE Perp chỉ cách chặn dưới −5% có 0,64 điểm**: lần đánh giá lúc triển khai có thể loại nó nếu replay xấu thêm một chút.
