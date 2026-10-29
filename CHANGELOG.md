# Changelog

File này ghi lại các thay đổi đáng chú ý của dự án, bắt đầu từ ngày 2026-10-06. Các thay đổi trước đó xem trong `git log` và các file `docs/*-research.md`.

Mỗi mục gồm ngày, tóm tắt, commit liên quan và link sang tài liệu chi tiết. Mục mới nhất nằm trên cùng.

## 2026-10-28 — Kết thúc soak 14 ngày: 9 route pass gate, HYPE Spot hoãn

**Soak paper (14/10–28/10):**
- 9 tín hiệu. 8 lệnh đã đóng, cả 8 chạm stop (khoảng −3,4% tới −5%). ETH Perp SHORT (tín hiệu lúc 16:00Z ngày 24/10) còn mở, −1,9% lúc 02:24Z.
- Julius đánh giá chuỗi thua là do thị trường và quyết định chuyển sang G5.
- **Vào bằng limit** khớp 7/7 lần và lỗ ít hơn lệnh thị trường ở 4/6 lệnh đã đóng, trung bình +0,79 điểm % (t = 1,74).
- **Jev:** 7 lần gọi thành công; 2 lần `provider_error` ở các tín hiệu lúc 16:00Z ngày 24/10, do khoá bị từ chối (401). Julius thay khoá lúc 02:27Z ngày 25/10; sau đó chưa có tín hiệu nào để kiểm khoá mới.
- **Heartbeat:** 7 route đạt 100%, ETH Perp 98,8%, NEAR Spot 98,8%, ETH Spot 95,2%, HYPE Spot 28,9%.

**Gate soak** (`replay-v2`, cửa sổ từ mốc neo tới nến đóng lúc 04:00Z ngày 28/10):
- **Spot:** gate automation của worker tự evaluate lúc 04:10:03Z.
  - ETH, NEAR, SOL và ZEC pass.
  - HYPE Spot `deferred` vì `heartbeat_coverage_below_95pct`. Campaign vẫn active, chưa bị đóng.
- **Perp:** automation không tạo job. Mình chạy thủ công lúc 07:39Z, với khoá Julius đã duyệt: lấy funding mới tới 04:00Z, rồi `assets rules evaluate`. Cả 5 route pass.
- Trước đó đã chạy thử chỉ đọc trên container `admin` dùng một lần (không ghi gì) và ra kết quả giống hệt.

| Route | Lãi ròng | DD | Lệnh | Soak evaluation |
|---|---:|---:|---:|---|
| ETH Spot | +1,98% | 5,26% | 36 | `098420cf3e5475cbab10d95b1608f2ea` |
| NEAR Spot | +9,38% | 4,94% | 40 | `986cbdc1b23aee2a2244c6be5eb6ebb8` |
| SOL Spot | +1,53% | 5,92% | 39 | `e29d2f70b2d308b22e5f2e24a9f5db36` |
| ZEC Spot | +14,59% | 4,54% | 48 | `520eda76b75b98ef1f99776e4d52532b` |
| ETH Perp | +4,28% | 5,53% | 48 | `411a4a9f96e4b61dbee1b20fbfc6c8c4` |
| HYPE Perp | −4,57% (miễn trừ, sàn −5%) | 7,11% | 21 | `234fe099bd073fa7b95014e03f776767` |
| NEAR Perp | +5,03% | 4,93% | 39 | `25b9ecdfb1726dc2c550c8a5db9479e5` |
| SOL Perp | +4,54% | 3,71% | 42 | `2c8a404e7ee83aa5538ab91458972574` |
| ZEC Perp | −2,49% (miễn trừ) | 6,50% | 33 | `371d946b620c49ba9e81def71e80ece8` |

**Lưu ý:** HYPE Perp chỉ còn cách sàn −5% có 0,43 điểm. Ngày 28/10 chưa activate route nào, chưa đụng Demo; VPS vẫn chạy bản 871dd9d.

**Cập nhật 29/10 (G5, bước 1–2; Julius duyệt):**
- Lệnh ETH Perp SHORT chạm stop lúc 12:00Z ngày 28/10, lỗ −3,3% (nếu vào bằng limit thì −2,9%). Cả đợt soak: 9 lệnh đã đóng, 9 lệnh lỗ.
- 02:02Z: `assets rules activate` cả 9 route bằng đúng các soak evaluation ở bảng trên. Cả 9 thành champion, challenger để trống. HYPE Spot không được activate.
- 02:03–02:05Z: `assets scan` (1.000 USDT), rồi `assets venue set --venue bnb --environment demo` cho cả 10 route. Tất cả `listed` và `selectable`.
- Spread lúc quét: Spot từ 0,04 tới 2,5 bps. Perp rộng hơn lần quét ngày 15/10: ETH 1,6, ZEC 9,9, SOL 12, NEAR 13, HYPE 17 bps.
- Build image `demo-execution` trên 871dd9d. Runner chưa khởi động, journal chưa có.
- Kiểm quyền trong container Demo: đọc được source, ghi được journal, key 0600 thuộc uid 1000, runner chạy uid 1000.
- Key Demo đọc được tài khoản Futures:
  - 5.000 USDT, `canTrade`, One-way, Single-asset; không có vị thế hay lệnh mở.
  - **Cả 5 cặp Perp đang ở `CROSSED`, leverage `0`.** Sàn trả 0 khi chưa từng chỉnh leverage cho cặp đó.
  - `AccountSnapshot` đòi leverage ≥ 1 nên `aigt perp COIN` báo "Perp information unavailable". Cần đặt Isolated 1x trên giao diện Demo trước khi chạy wizard leverage và `configure`.
- Julius đặt Isolated 1x cho 5 cặp trên giao diện Demo. Mỗi ví Spot và Futures có 5.000 USDT.
- Julius chạy wizard leverage trong TTY. Journal ghi verified 1x cho cả 5 cặp, từ 02:50Z (ETH) tới 03:21Z (SOL).
- 03:22Z: `execution demo configure --from-active-set --jev-mode advisory --capital 1000`.
  - Profile `setup2_slots_v1`; Spot và Perp mỗi bên 50%, mỗi coin 20%.
  - Baseline: Perp 5.000 USDT, Spot 5.005,41 USDT.
- `preflight` rồi `activate` cho 9 route, dùng các soak evaluation ở bảng trên.
  - Mỗi route có `target_notional_cap` 166,67 USDT và `stop_distance` 0,05.
  - Sau đó portfolio `enabled`, không còn paused, 9 route active, không có `spot:HYPEUSDT`. 0 lệnh, 0 fill.
- **Runner `demo-execution` chưa khởi động (G6 chờ Julius duyệt riêng).**
- Nến 04:00Z: worker ghi quan sát của cả 10 route lúc 04:00:01. 9 route dùng đúng rule champion, không có tín hiệu. Promote không làm đứt heartbeat.
- **Trang `/setup2` chỉ còn hiện HYPE Spot**, vì dashboard chỉ đọc campaign `active`.
  - Sửa ở `a0e9ddb`, trên nhánh `fix/setup2-dashboard-champions` tách từ 871dd9d (cherry-pick sang nhánh này là `eff4a06`).
  - Campaign `promoted` được hiện khi rule của nó vẫn là champion của route, ghi là "Rule chính". Mỗi route chỉ một dòng.
  - Có 1 test mới. Toàn bộ test: 1.928 passed, chỉ bỏ qua `test_perp_collection_migration` (lỗi có từ trước). Chưa deploy.
- **G6, chạy thử một chu kỳ** (Julius duyệt), 07:18Z: `execution demo run --multi --once`.
  - Kết quả `status: running`, equity 1.000 USDT, không có route nào có tín hiệu. 0 lệnh, 0 fill.
  - Journal chỉ ghi `portfolio_cycle`. Runner liên tục chưa khởi động.
- **Deploy `a0e9ddb`** (Julius duyệt push và deploy), lúc 07:37–07:40Z:
  - Backup trên host `~/.local/state/aigorithmic-trading/backups/intraday-20261029T073709174957Z.sqlite3`: 3.630.338.048 byte, SHA-256 `540ab9ed…c81c`, `verify` ok.
  - Image `web` cũ được tag `intraday-web:rollback-871dd9d-20261029`.
  - Chỉ rebuild và khởi động lại `web`. `worker` giữ nguyên container, `demo-execution` không chạy.
  - Sau deploy: web healthy; `/setup2` và chart trả 200. API hiện đủ 10 route (9 `champion`, HYPE Spot `soak`) và 9 tín hiệu.
  - VPS đang ở detached HEAD `a0e9ddb`.
- **G6: bật runner `demo-execution` chạy liên tục lúc 07:42Z** (Julius duyệt), chu kỳ 30 giây.
  - Kiểm trước khi bật: không có runner nào khác.
  - Sau khi bật: worker giữ nguyên container; chu kỳ đầu báo `running`, equity 1.000 USDT, không có route nào có tín hiệu.
  - Từ đây, tín hiệu Setup-2 trên 9 route sẽ thành lệnh thật trên Binance Demo.
- **Soak lại HYPE Spot** (Julius duyệt). Điều kiện: Binance Spot có 36 nến 1d, job `spot_4h` không lỗi từ 27/10.
  - 07:45Z: `setup2 candidate --retire-open` tạo `hypeusdt-spot-4h-setup2-slots-v1-1793259927` và đóng campaign cũ.
  - `setup2 backfill`: không có nến mới.
  - Replay v2 pass (`400c443b50c395021465e9c972482f89`): 0 lệnh, 3.091 nến, phủ 100%, có miễn trừ số lệnh và lãi ròng khi không có lệnh.
  - 07:46Z: `start-soak`, campaign `f8864be56d684826ea1ce24a2eabc602`. Đánh giá sớm nhất 2026-11-12 07:46Z.
- Bài học: `backup create` không có `--output-dir` thì file nằm trong container `admin` dùng một lần và mất khi container bị xoá. Phải mount thư mục backup trên host và chạy `--user 1000:1000`.

## 2026-10-24 — Nghiên cứu vào lệnh sớm bằng H1 cho Setup-2: không biến thể nào đạt

**Vì sao làm:**
- Ngày 23/10, ZEC thủng đáy H1 nhưng Setup-2 không có tín hiệu, vì luật chỉ xét nến H4 đã đóng.
- Julius hỏi có thể kết hợp H1 với H4 để vào sớm không, và chọn thử cách A: vẫn là breakout Donchian 25 của H4, nhưng vào ngay khi một nến H1 đóng vượt kênh.

**Code** (đều thuộc research, không đụng runtime):
- `44c841e`: thiết kế đăng ký trước, commit trước khi chạy.
- `fa3df91`:
  - `slot_replay` có thêm lệnh vào sớm ở giá mở một nến M15 trong nến H4. Lệnh sớm chỉ lấy slot còn trống sau các lệnh ở giá mở, và không đổi vị thế. Luật B ra đúng cùng danh sách lệnh như trước.
  - Thêm `scripts/h1_early_export.py` và `replay_v2/h1_early_study.py`. Quan sát được tính lại bằng chính `setup2._observe` trên nến H4 dở dang, và đã kiểm trùng với VPS ở mọi nến.

**Kết quả** (dữ liệu tới nến H4 mở lúc 04:00Z ngày 24/10; xuất chỉ đọc qua container `admin`):

| | Lệnh thị trường | DD | Lợi nhuận/DD |
|---|---:|---:|---:|
| B (luật hiện tại) | +38,7% | 7,8% | 4,98 |
| E1 vào sớm, đủ bộ lọc (volume ngoại suy ×4/h) | +34,5% | 8,3% | 4,15 |
| E2 vào sớm, bỏ volume | +35,9% | 7,9% | 4,53 |

- **Vào sớm có giá tốt hơn B khoảng 1,1% (0,46 ATR)** trên các breakout mà nến H4 cũng xác nhận. Trên cùng 162 breakout, E1 lãi +41,6 điểm % vốn so với +25,3 của B.
- **Nhưng 34–38% số lệnh sớm là phá giả** (nến H4 đóng lại không thành tín hiệu), trung bình −0,8% mỗi lệnh. Các lệnh này còn chiếm slot và làm cổng lãi lỗ đóng nhiều hơn, nên mất 52–61 lệnh tốt của B.
- Cả hai biến thể trượt 6/7 tiêu chí. Luật Setup-2 giữ nguyên, không có ADR, không dò thêm.

Chi tiết: [docs/setup2-h1-early-entry-research.md](docs/setup2-h1-early-entry-research.md).

## 2026-10-22 — Nghiên cứu vùng hỗ trợ/kháng cự cho Setup-2: không biến thể nào đạt

**Vì sao làm:**
- 2 lệnh ZEC Spot LONG ngày 21/10 vào ngay dưới vùng kháng cự 1349–1385, và cả hai chạm stop −5%.
- Julius đưa 3 indicator TradingView (LuxAlgo, SRchannel, ChartPrime). Sau các phép thử nhanh, Julius duyệt plan: thử 4 cách vào lệnh dựa trên vùng, và nếu đạt thì đưa vào luật sau buổi review 28/10.

**Code** (đều thuộc research, không đụng runtime):
- `9e8859d`: `replay_v2/sr_zones.py`, bản port SRchannel (MPL-2.0), tham số mặc định.
- `f35ce28`: tách vòng lặp danh mục slot ra `replay_v2/slot_replay.py`. Bản mới cho đúng cùng danh sách lệnh như bản cũ, kiểm trên dữ liệu ngẫu nhiên và trên dữ liệu thật.
- `28f233a`: thiết kế đăng ký trước, commit trước khi chạy.
- `ff9305d`: `scripts/sr_zone_export.py` và `replay_v2/sr_zone_study.py`.

**Kết quả** (dữ liệu tới nến H4 mở lúc 04:00Z ngày 22/10; xuất chỉ đọc qua container `admin`):

| | Lệnh thị trường | DD | Lợi nhuận/DD |
|---|---:|---:|---:|
| B (luật hiện tại) | +38,7% | 7,8% | 4,98 |
| S1 lọc | +21,6% | 8,9% | 2,43 |
| S2 chờ vượt hẳn | +20,0% | 9,4% | 2,12 |
| S3 hồi về vùng | +5,9% | 16,1% | 0,36 |
| S4 stop theo vùng | +28,1% | 10,8% | 2,60 |

- **Vùng không chặn giá hơn vùng ngẫu nhiên:** 41,3% so với 40,0%. Với ZEC và HYPE, vùng thật còn chặn ít hơn vùng ngẫu nhiên.
- **Breakout có vùng chặn là lệnh tốt nhất của B:** 124 lệnh, TB +1,60% mỗi lệnh, so với khoảng +0,62% ở các lệnh còn lại. Lọc chúng đi làm mất lãi.
- Luật Setup-2 giữ nguyên, không có ADR-008.

Chi tiết: [docs/setup2-sr-zone-research.md](docs/setup2-sr-zone-research.md).

## 2026-10-22 — Deploy `269d6a5` lên VPS (restart cả worker và web)

Julius duyệt push và deploy. Push `60e1f1c..269d6a5` lúc 01:53Z, deploy lúc 01:53–01:59Z, nằm giữa nến 00:00Z và nến 04:00Z.

- **Trước khi đổi code:**
  - image cũ được gắn tag `rollback-c27b48e-20261022` (worker `3d1fb3b4e0ac`, web `620209ac5edb`, admin `6840d8d55d64`);
  - image worker đang chạy (`5f7cd99fd738`, build từ `fba4e55`) được gắn thêm tag `intraday-worker:rollback-fba4e55-running-20261022`;
  - backup `intraday-20261022T015340329236Z.sqlite3`, kiểm tra `ok`.
- **Những gì worker nhận thêm:** worker trước đó chạy code `fba4e55`. Từ đó đến nay, phần worker thực sự chạy chỉ đổi ở bản sửa nến trong `store.py`. Các thay đổi còn lại thuộc CLI (`aigt ui`, định tuyến `learning`), web và research.
- **Image mới:** worker `d45b48638a7f`, web `99c406385b7e`, admin `c847344681ff`.
- **Kết quả:**
  - web lên lúc 01:58:28Z (healthy), worker lúc 01:59:09Z (`RestartCount` 0);
  - log worker không có lỗi;
  - `doctor` trả `ok`, vẫn chế độ paper, `execution_enabled: false`;
  - chart ETH Perp và `/api/setup2` trả dữ liệu mới ngay từ request đầu tiên.
- **Lùi lại nếu cần:** `docker tag intraday-<svc>:rollback-c27b48e-20261022 intraday-<svc>:latest`, rồi `git checkout --detach c27b48e`. Khởi động lại web trước, worker sau. Muốn về đúng code worker cũ thì dùng tag `rollback-fba4e55-running-20261022`.

## 2026-10-22 — Nhận nến Binance sửa lại; web không trả bản cache quá cũ

Julius duyệt cả hai bản sửa sau sự cố ETH Spot ngày 21/10 và lần kiểm tra chart ETH.

- **Nến Binance sửa sau khi đóng** (`IntradayStore.record_asset_candles`):
  - Trước đây, khi Binance trả một nến đã lưu với nội dung khác, job báo `ValueError` và dừng ở mọi nến H4 sau đó.
  - Giờ bản Binance sửa sẽ thay bản đã lưu, và worker in một dòng log `{"candle_revised": …}` có cả bản cũ lẫn bản mới, xem được bằng `aigt logs`.
  - `setup2_candles` không cần sửa: `setup2_store.sync` chỉ tải các nến sau nến cuối đã lưu, nên không tải lại nến cũ.
- **Cache của web** (`CachedSnapshot`):
  - Trước đây, request đầu tiên sau một thời gian dài không ai mở web nhận lại bản cũ. Ví dụ ngày 22/10, chart ETH Perp trả nến cuối là 20/10 08:00.
  - Giờ nếu bản cache cũ hơn 10 phút (`max_stale_seconds=600`), request chờ tính lại xong rồi mới trả. Nếu tính lại lỗi thì vẫn trả bản cũ.
  - Trong 10 phút đầu, web vẫn trả bản cache ngay rồi tính lại ở nền như trước.
- **Test:**
  - `test_native_spot_candle_intervals_are_isolated_and_take_binance_revisions` thay cho test "immutable" cũ;
  - thêm `test_cached_snapshot_rebuilds_before_answering_once_the_copy_is_too_old`;
  - toàn bộ suite: 1879 passed. `test_perp_collection_migration` vẫn có 1 test fail như trước khi sửa.
- **Deploy:** cần restart worker để bản sửa nến có hiệu lực, và restart web để bản sửa cache có hiệu lực.

## 2026-10-21 — Sự cố: job ETH Spot 4h dừng vì Binance sửa nến đã đóng

**Phát hiện:** khi kiểm tra VPS lúc 12:02Z, job `asset_spot_4h_ethusdt` đã báo `ValueError` liên tục 4 nến (21/10 00:00, 04:00, 08:00, 12:00Z). Heartbeat của ETH Spot giảm còn 90,9%.

**Nguyên nhân:**
- Lúc 20:00:53Z ngày 20/10, worker lưu nến ETH Spot 4h mở lúc 20/10 16:00Z vào `asset_daily_candles`.
- Sau đó Binance sửa lại nến này: giá đóng từ 2437,32 thành 2437,92, có thêm 169 giao dịch.
- Ở mỗi nến H4 sau đó, `record_asset_candles` tải lại nến này, thấy khác bản đã lưu, báo `historical 4h candle changed` và dừng cả job. Vì job dừng sớm, Setup-2 không được đánh giá.
- `setup2_candles` đã lưu đúng bản Binance đã sửa. Không có nến 8h, 1d hay coin nào khác bị lệch.

**Cách sửa** (Julius duyệt, khoảng 12:10Z):
- Backup trước: `intraday-20261021T120657741631Z.sqlite3`, kiểm tra `ok`.
- Sửa đúng một dòng trong `asset_daily_candles` (ETHUSDT, 4h, open 20/10 16:00Z) thành bản Binance hiện tại.
- Script có kiểm tra: bản cũ trong DB phải khớp chính xác, và bản mới phải trùng với `setup2_candles`.
- Chạy lại probe chỉ-đọc: không còn nến nào lệch.
- Không deploy code, không restart worker.

**Ảnh hưởng tới soak:** 4 nến đã mất không được bù lại. Nếu từ nến 16:00Z không mất thêm nến nào, đến buổi review 28/10 ETH Spot còn 80/84 nến = 95,2%, vừa đủ ngưỡng 95%.

**Việc còn mở:** code vẫn sẽ dừng job nếu Binance sửa một nến khác. Cần sửa để job nhận bản đã sửa (ghi đè và ghi log) thay vì dừng; bản sửa này sẽ được duyệt riêng.

## 2026-10-20 — Deploy `3ae60eb` lên VPS (`aigt ui`, chỉ restart web)

Julius duyệt push và deploy. Push `8f668a8..3ae60eb` lúc 06:59Z.

- **Lần thử đầu bị hoãn:** lúc 06:59Z máy local đang ở mạng khác, không SSH được tới VPS, nên chưa chạy bước nào. Deploy lại lúc 12:27–12:33Z, sau khi máy về mạng nhà.
- **Trước khi đổi code:**
  - image cũ được gắn tag `rollback-9462520-20261020` (worker `db564bb489a7`, web `3188c9025ce4`, admin `60a8812ae7e7`);
  - backup `intraday-20261020T122739709085Z.sqlite3`, kiểm tra `ok`.
- **Image mới:** worker `3d1fb3b4e0ac`, web `620209ac5edb`, admin `6840d8d55d64`.
- **Worker không bị restart:** vẫn chạy từ 19/10 08:32:57Z, `RestartCount` 0, dùng image `5f7cd99fd738`. Image này cũ hơn tag rollback vì lần deploy trước cũng không restart worker.
- **Kết quả:**
  - healthz 200;
  - `/api/setup2/chart` trả 120 nến cho các route có thật (khoảng 0,2–0,3 giây) và 404 với route lạ;
  - `/api/learning` trả danh sách rỗng, vì chưa có review nào;
  - `aigt learning proposals list` trên host VPS chạy trong container admin;
  - `aigt ui` trên máy local đọc được dữ liệu thật và vẽ được chart nến.

## 2026-10-20 — `aigt ui` bước 3: command palette cho mọi lệnh

- **`Ctrl+P` liệt kê toàn bộ 94 lệnh `aigt`.** Danh sách đọc thẳng từ cây argparse, nên lệnh mới tự xuất hiện. Chọn một lệnh sẽ mở form sinh từ tham số của nó, kèm dòng xem trước đúng câu lệnh SSH sẽ chạy. Output JSON được tách khỏi các dòng tiến trình của Docker và tô màu.
- **Ba nhóm an toàn** (`intraday/tui/commands.py`, khai báo tường minh):
  - **chỉ đọc (18 lệnh):** chạy ngay. Chỉ gồm các lệnh `aigt` đưa vào container, vì host VPS còn một DB cũ ở `~/.local/state` nên lệnh chạy trên host sẽ đọc sai dữ liệu. Thêm `backup verify`.
  - **ghi (18 lệnh):** chạy sau hộp xác nhận. Gồm backup, start/stop/restart, analysis, learning review, backfill, scan, replay, đánh giá soak, provider test.
  - **terminal (58 lệnh):** TUI không chạy, chỉ hiện `ssh -t … aigt …` để Julius tự chạy. Gồm `execution`, `perp`, `connect`, thêm/xoá/kích hoạt provider, đổi luật, campaign, active set, và mọi lệnh chạy trên DB của host. Lệnh nào chưa được khai báo thì mặc định vào nhóm này.
- **Form không có ô** `--database`, `--secrets-file`, `--report-dir`, `--execution-database`, vì các tham số này làm lệnh chạy trên host thay vì trong container.
- **Đã thử trên VPS:**
  - qua SSH, `assets list`, `active-set show`, `setup2 evaluate`, `provider list`, `assets readiness` đều trả JSON, exit 0;
  - TUI tự mở tunnel ở cổng khác (18082) và tự đóng khi thoát;
  - chạy headless với VPS: `/api/setup2`, `/api/dashboard`, `/api/portfolio`, `/api/signals` đều đọc được. `/api/learning` và `/api/setup2/chart` trả 404 cho tới khi deploy web.
- **Test:** thêm 4 test, gồm kiểm tra không lệnh `execution`, `perp`, `connect` nào nằm ngoài nhóm terminal. `tests/test_tui.py` có tổng 14 test.

## 2026-10-20 — `aigt ui` bước 2: Lệnh, Vòng học, Vận hành

- **3 tab mới:**
  - **Lệnh:** danh mục mẹ (vốn, lãi đã chốt, phí, vị thế, giới hạn của profile `setup2_slots_v1`) và 50 quyết định gần nhất trong journal;
  - **Vòng học:** các lần review và các đề xuất (chờ duyệt xếp trước), xem chi tiết từng đề xuất; phím `a`/`x` để duyệt hoặc từ chối, có ô ghi chú;
  - **Vận hành:** job gần nhất, nguồn dữ liệu ngoài, nút tải 200 dòng log, backup DB, restart, start, stop.
- **Thao tác ghi:** chạy `~/.local/bin/aigt …` trên VPS qua SSH, chỉ sau khi Julius bấm "Chạy" trong hộp xác nhận. Hộp này hiện đúng câu lệnh sẽ chạy, nút mặc định là "Huỷ". Riêng tải log là chỉ đọc nên chạy ngay.
- **`aigt learning` chạy trong container admin** khi có deployment đã đăng ký, giống `status` và `doctor`. Lý do: report và sổ đề xuất nằm trong volume `replay-reports`, mà chỉ container mount volume này (trên host còn một DB cũ ở `~/.local/state`). Truyền `--database`, `--output` hoặc `--execution-database` thì vẫn chạy trực tiếp như cũ.
- **Web:** endpoint chỉ đọc `GET /api/learning` (`learning_review.review_index`) trả các review (không kèm facts) và đề xuất. Trên VPS hiện chưa có review nào.
- **Test:** thêm 4 test, gồm: lệnh ghi chỉ chạy sau khi xác nhận, huỷ thì không chạy, đề xuất đã quyết thì không mở hộp xác nhận.

## 2026-10-20 — `aigt ui`: dashboard toàn màn hình trong terminal (bước 1)

Julius muốn CLI có giao diện đẹp hơn và chọn dashboard toàn màn hình, chạy trên máy local, đọc dữ liệu từ VPS (kế hoạch gồm 3 bước). Bước 1:

- **Lệnh mới `aigt ui`** (`intraday/tui/`), dùng Textual 8.2.8 làm extra tùy chọn `tui`. Image Docker không cài extra nên không đổi.
  - Tự mở tunnel SSH tới web của VPS nếu chưa có, và tự đóng khi thoát.
  - Host được lưu trong `remote.toml`, cạnh `deployment.toml`; IP VPS không nằm trong repo.
  - Tự làm mới mỗi 60 giây; thanh trạng thái hiện tunnel, tuổi dữ liệu, nến H4 tiếp theo và lỗi của từng endpoint.
- **4 tab:**
  - **Tổng quan:** worker, việc cần chú ý, Telegram, chi phí model, nến H4 mới nhất của từng route, lỗi job trong 24 giờ;
  - **Setup-2:** danh sách route, chart nến H4 vẽ bằng ký tự (EMA50, EMA200, kênh Donchian, mốc vào lệnh, stop), các tín hiệu kèm Jev, khung giờ, mô phỏng và limit;
  - **Theo dõi ngầm:** V4, limit 0,25 ATR, điểm khung giờ;
  - **Soak:** tiến độ và heartbeat từng route tới ngày review.
- **Web:** endpoint chỉ đọc `GET /api/setup2/chart?route=` trả số liệu chart.
  - Phần tính số liệu của `price_chart` được tách thành `chart_series`, nên SVG của `/setup2` giữ nguyên (đã so khớp với bản cũ).
  - Endpoint này cần deploy web mới dùng được. Trước khi deploy, tab Setup-2 báo không tải được chart.
- **Test:** `tests/test_tui.py` (7 test). Toàn bộ test: 1869 passed.

## 2026-10-19 — Deploy `9462520` lên VPS (theo dõi V4, chỉ restart web)

Julius duyệt push và deploy. Push `5054708..9462520`, deploy lúc 12:58–13:05Z.

- **Trước khi đổi code:**
  - image cũ được gắn tag `rollback-5054708-20261019`;
  - backup `intraday-20261019T125806871329Z.sqlite3`, kiểm tra `ok`.
- **Sự cố khi deploy:** lần `git fetch` đầu tiên chưa lấy được commit mới, nên lệnh `checkout` lỗi. Script vẫn chạy tiếp, build lại code cũ `5054708` và restart web, tức web chạy lại đúng bản cũ.
  - Sau đó fetch lại theo tên nhánh, checkout `9462520`, build lại và restart web lần nữa.
  - Lần sau script deploy phải dừng ngay khi checkout lỗi.
- **Image mới:** worker `db564bb489a7`, web `3188c9025ce4`, admin `60a8812ae7e7`.
- **Kết quả:**
  - chỉ restart web, healthz 200;
  - worker không bị restart (vẫn chạy từ 08:32:57Z, `RestartCount` 0);
  - trang `/setup2` có mục "Biến thể V4", hiện "thêm 0, bỏ 0".
- **Thời gian tải:** lần đầu sau restart, `/setup2` mất 3,1 giây (bản trước 0,3 giây), vì mục V4 đọc lại quan sát của mọi route. Các lần sau 0,01 giây, nhờ bản số liệu được giữ sẵn trong bộ nhớ.

## 2026-10-19 — Theo dõi shadow biến thể V4 (ADX dốc 3 nến)

Julius quyết định theo dõi V4. Đây là biến thể trượt sát nhất trong [nghiên cứu bộ lọc nhanh](docs/setup2-fast-filter-research.md). Luật đang chạy giữ nguyên, V4 chỉ được ghi lại để so sánh.

- **Quy tắc V4:** thay điều kiện ADX của Setup-2 (ADX > 20 và cao hơn nến trước) bằng điều kiện ADX cao hơn ADX của 3 nến H4 trước, không có ngưỡng.
- **Vòng học hằng tuần** (`intraday/learning_review.py`, câu hỏi lên `learning-questions-v5`): thêm mục `adx_rise`, tính trên mọi tín hiệu từ đầu soak, gồm hai nhóm:
  - **thêm:** breakout không có blocker, chỉ trượt bộ lọc ADX nhưng đạt điều kiện V4, tức V4 vào còn luật thì không;
  - **bỏ:** lệnh của luật mà ADX không cao hơn 3 nến trước, tức V4 sẽ bỏ qua.
- **Cách tính kết quả:** mỗi tín hiệu được mô phỏng bằng chính stop và luật thoát của Setup-2, chưa trừ phí, và không tính cạnh tranh slot. Khi thiếu quan sát của 3 nến trước, tín hiệu được ghi là `no_data`.
- **Báo cáo:** LLM nhận xét V4 ở câu A3. Tin Telegram có thêm một dòng "ADX dốc 3 nến (V4, theo dõi)".
- **Trang `/setup2`:** thêm mục "Biến thể V4", gồm tóm tắt hai nhóm và bảng tín hiệu.
- **Kiểm tra trên database VPS (chỉ đọc):** chưa có tín hiệu nào mà V4 khác luật. Bốn lệnh của luật từ đầu soak (ETH, HYPE, ZEC ngày 15/10 và ZEC ngày 18/10) đều có ADX tăng qua 3 nến. Bốn breakout bị lọc đều trượt cả bộ lọc khác ngoài ADX.
- Phải deploy thì trang web và vòng học mới có phần này. Worker không đọc code này, nên không cần restart worker.

## 2026-10-19 — Chạy nghiên cứu bộ lọc nhanh cho Setup-2: không biến thể nào đạt

Chạy đúng một lần theo thiết kế đã commit ở `517f42b`. Kết quả ở [docs/setup2-fast-filter-research.md](docs/setup2-fast-filter-research.md); code ở `intraday/replay_v2/fast_filter_study.py` và `scripts/fast_filter_export.py`, có 9 test.

- **Mốc B** (luật hiện tại): +39,2% / DD 7,8% với lệnh thị trường, +43,3% / DD 5,8% với limit 0,25 ATR.
- **V1 và V2** (vào cả breakout thiếu lọc với 0,5x): lãi thấp hơn hẳn (+24,7% và +32,0%). Lệnh thêm vẫn có lãi nhỏ nhưng chiếm hết 3 slot, nên các tín hiệu tốt bị chặn.
- **V3** (EMA20/50): gần như không khác B, 13 lệnh thêm đều lỗ.
- **V4** (ADX cao hơn 3 nến trước, bỏ ngưỡng 20): +45,7% / DD 8,9% và +51,7% / DD 7,1%, hơn B ở cả ba năm. Vẫn trượt hai tiêu chí, đều rất sát: lãi/DD với limit thấp hơn 0,21, và nửa sau thấp hơn 0,2 điểm.
- **V5** (thân nến ≥ 1 ATR): năm 2024 yếu, DD 10,5%.
- Luật đang chạy giữ nguyên. Theo dõi V4 trên soak là một quyết định mới, chờ Julius.

## 2026-10-19 — Thiết kế nghiên cứu bộ lọc nhanh cho Setup-2 (chưa chạy)

Julius thấy Setup-2 bắt xu hướng chậm và đề xuất 5 hướng. Thiết kế được viết và commit trước khi chạy, chi tiết ở [docs/setup2-fast-filter-research.md](docs/setup2-fast-filter-research.md).

- **Không thử lại:** vào lại khi giá hồi về EMA, vì Setup-3 đã không đạt và chờ 1 nến xác nhận làm kết quả tệ hơn.
- **Năm biến thể:**
  - V1: breakout luôn vào, 0,5x khi thiếu bộ lọc;
  - V2: vào hai tầng, thêm 0,5x khi EMA/ADX/DMI xác nhận trong 24 giờ;
  - V3: EMA20/50 thay EMA50/200;
  - V4: ADX cao hơn 3 nến trước, bỏ ngưỡng 20;
  - V5: thân nến breakout ≥ 1 ATR thay bộ lọc EMA50 và ADX/DMI.
- **Cách chạy:** trên danh mục slot của ADR-007, với lệnh thị trường và với limit 0,25 ATR cho Perp.
- **Tiêu chí đạt:** phải hơn luật hiện tại về lợi nhuận và lợi nhuận/sụt giảm ở cả hai cách vào lệnh, sụt giảm ≤ 10%, hơn ở cả hai nửa dữ liệu, và vẫn hơn khi chi phí gấp đôi.
- Đạt cũng chỉ dẫn tới đề xuất theo dõi shadow, không đổi luật đang chạy.

## 2026-10-19 — Deploy `5054708` lên VPS (chart rõ hơn, chỉ restart web)

- Image cũ được gắn tag `rollback-17e87dc-20261019`. Image mới: worker `3622ab59715b`, web `840f6ec96c79`, admin `22cd9bdf3d64`.
- Chỉ restart web, healthz 200, `app.css` mới đã có trên VPS. Worker không bị restart (vẫn chạy từ 08:32:57Z, `RestartCount` 0).
- **Thời gian tải:** lần đầu sau restart, `/setup2` mất 0,30 giây; các lần sau 0,01 giây. Trang `/` mất 10,9 giây ở lần đầu.

## 2026-10-19 — Deploy `17e87dc` lên VPS (chart giá, chỉ restart web)

- Image cũ được gắn tag `rollback-bbd320f-20261019`. Image mới: worker `056eda55a1de`, web `49588d2e0aa2`, admin `6a40c6a18101`.
- Chỉ restart web, healthz 200. Worker không bị restart (vẫn chạy từ 08:32:57Z, `RestartCount` 0).
- **Thời gian tải:** lần đầu sau restart, `/setup2` mất 1,2 giây; các lần sau 0,01 giây. Chuyển sang một route khác lần đầu mất 0,25 giây. Trang `/` vẫn mất 10,7 giây ở lần đầu sau restart, vì đó là lúc tính bản số liệu đầu tiên.

## 2026-10-19 — Chart giá trên trang `/setup2`

Julius muốn có chart giá trên web. Chart được vẽ bằng SVG ngay trên server, không thêm thư viện hay script ngoài, giống cách trang Replay vẽ chart equity.

- **Nội dung chart:** nến 4h của 20 ngày gần nhất, mỗi route một tab (mặc định là route có tín hiệu gần nhất). Trên chart có:
  - EMA50, EMA200 và kênh Donchian 25 nến, tính giống hệt luật Setup-2: `indicator_series` và đỉnh/đáy của 25 nến trước;
  - khối lượng;
  - giá đóng mới nhất.
- **Tín hiệu trên chart:** dấu tam giác (short nằm trên đỉnh nến, long dưới đáy nến), đường stop ban đầu (`setup2.initial_stop`, kéo tới lúc mô phỏng thoát lệnh) và mức limit 0,25 ATR. Rê chuột lên nến hay tín hiệu sẽ thấy OHLC và giờ.
- **Tốc độ:** mỗi route dựng mất khoảng 0,25 giây trên database VPS (chỉ đọc). Chart được giữ sẵn trong bộ nhớ 60 giây như các phần còn lại của trang.

## 2026-10-19 — Deploy `bbd320f` lên VPS (chỉ restart web)

Julius duyệt push và deploy. Từ `fba4e55` lên `bbd320f`; so với bản trước chỉ `intraday/web.py` thay đổi.

- Image cũ được gắn tag `rollback-fba4e55-20261019`. Image mới: worker `6afa1a6e02be`, web `8a62174622b5`, admin `563fa85e5a0f`.
- Chỉ restart web, healthz 200. Worker không bị restart (vẫn chạy từ 08:32:57Z, `RestartCount` 0); khi worker restart lần sau nó sẽ chạy image mới, image này chỉ khác ở phần web.
- **Thời gian tải:** lần đầu sau restart, `/` mất 10,3 giây vì phải tính bản số liệu đầu tiên. Từ đó `/` và `/setup2` đều trả trong khoảng 0,006 giây.
- **Ghi nhận ở máy local:** máy của Julius đang chạy một container `intraday-web-1` bản cũ chiếm cổng `127.0.0.1:8081`, còn SSH tunnel cũ chỉ nhận được `[::1]:8081`. Vì vậy mở `localhost:8081` có thể vào nhầm bản local. Tôi mở thêm tunnel ở cổng `127.0.0.1:18081` để xem đúng web trên VPS.

## 2026-10-19 — Trang web trả số liệu đã tính sẵn để không bị chậm

Julius thấy trang bị lag. Đo trên VPS: trang Operations (`/`) mất khoảng 10,8 giây mỗi lần tải, và gần như toàn bộ là truy vấn SQL đếm lại toàn bộ lịch sử (`venue_health` 3,8 giây, nguồn dữ liệu ngoài 2,9 giây, `scheduler_runs` 1,8 giây). Trang `/setup2` mất khoảng 0,8 giây.

- **Cách sửa:** `CachedSnapshot` trong `intraday/web.py` giữ một bản số liệu đã tính sẵn trong bộ nhớ (Operations 60 giây, `/setup2` 30 giây). Khi bản đó đã cũ, trang vẫn trả bản cũ ngay và một luồng nền tính lại. Chỉ lần mở đầu tiên sau khi restart web là phải chờ tính. `/api/dashboard` và `/api/setup2` dùng chung bản này.
- **Không đổi:** database, schema và worker. Thêm index cho các bảng lớn sẽ chữa tận gốc hơn, nhưng đó là đổi schema nên để sau G6.

## 2026-10-19 — Deploy `fba4e55` lên VPS (trang `/setup2`, theo dõi limit 0,25 ATR)

Julius duyệt push và deploy. Làm theo [runbook](docs/runbooks/deploy-a88825b.md), từ `7b815f4` lên `fba4e55`, sau nến 08Z. Schema, active set và `.env.intraday` không đổi. Vòng học hằng tuần vẫn **chưa** được lên lịch.

- **Trước deploy (08:29Z):**
  - `doctor` ok; chế độ paper, `execution_enabled: false`.
  - Worker chạy liên tục từ 12:24Z ngày 18/10, `RestartCount` 0, qua lần đổi ngày 00:00Z mà không bị khởi động lại.
  - Heartbeat: 9 route có nến tới 08Z. ETH Perp và NEAR Spot 96,8%, các route còn lại 100%; HYPE Spot 0% là lỗi đã biết.
  - Image cũ được gắn tag `rollback-7b815f4-20261019`: worker `62bcab6bfd1d`, web `7be8e6f913d2`, admin `40534650d642`.
- **Sao lưu:** `intraday-20261019T082948123359Z.sqlite3`, 2,83 GB, SHA-256 `551973c1…2b8c98`. Lệnh verify trả `status` ok và `integrity` ok.
- **Deploy:**
  - Build: worker `5f7cd99fd738`, web `13af88f65c13`, admin `5b7f02a73682`.
  - Web lên trước (healthz 200), sau đó worker lúc 08:32:57Z (`RestartCount` 0).
- **Sau deploy:**
  - `doctor` ok; vẫn chế độ paper, `execution_enabled: false`; active set `c3d57eda…` seq 1. Log worker không có lỗi.
  - `/setup2` trả 200. Lần tải đầu mất 5,3 giây (lúc server vừa khởi động), các lần sau khoảng 0,8 giây.
  - **Ghi nhận thêm:** trang `/` (Operations) có sẵn từ trước mất khoảng 10,5 giây mỗi lần tải. Lần deploy này chỉ thêm một link vào trang đó, không đổi phần đọc dữ liệu của nó.
- **Lùi lại nếu cần:** `docker tag intraday-<svc>:rollback-7b815f4-20261019 intraday-<svc>:latest`, rồi `git checkout --detach 7b815f4`. Khởi động lại web trước, worker sau.
- **Còn kiểm:** sau nến 12Z, cả 9 route phải có nến mới.

## 2026-10-19 — Trang theo dõi `/setup2` trên web VPS

Julius chọn đặt dashboard trên web app sẵn có thay vì artifact. Trang chỉ đọc dữ liệu, tự làm mới mỗi phút, và có link từ trang Operations. Đọc dữ liệu nằm ở `intraday/setup2_dashboard.py`; route `/setup2` và `/api/setup2` nằm trong `intraday/web.py`.

- **Tóm tắt đầu trang:**
  - **Worker:** job gần nhất chạy cách đây bao lâu, số job lỗi trong 24 giờ.
  - **Soak:** số route có heartbeat ≥ 95%, ngày được đánh giá sớm nhất.
  - **Tín hiệu:** số tín hiệu, chia theo cùng chiều hay ngược chiều khung giờ.
  - **Limit 0,25 ATR:** chênh lệch trung bình so với vào ngay.
  - **Telegram:** số tin chờ gửi và số tin đang lỗi.
- **Bảng:**
  - **Tín hiệu:** Jev đồng ý hay phản đối, điểm khung giờ, kết quả mô phỏng nếu vào ngay, và kết quả lệnh limit.
  - **Soak từng route.**
  - **Trạng thái luật** ở nến 4h vừa đóng: tín hiệu, breakout bị lọc, thoát kênh, kèm các bộ lọc chưa đạt.
  - **Job lỗi trong 24 giờ.**
  - **Tin Telegram gần nhất.**
- **Phạm vi:** chỉ hiện các coin trong active set, nên campaign BTC Spot cũ không hiện. Kết quả của tín hiệu dùng chung phần mô phỏng với vòng học hằng tuần, nên hai nơi luôn khớp số.
- **Thử trên database VPS** (chỉ đọc, nạp code tạm thời): dựng trang mất 0,8 giây. Bản đầu mất 3,3 giây vì đọc toàn bộ khoảng 90 nghìn câu trả lời Jev; bản này chỉ truy vấn đúng câu trả lời đi kèm từng tín hiệu.

## 2026-10-19 — Vòng học theo dõi lệnh limit 0,25 ATR cho short Perp

Julius chốt: chỉ giữ cách vào lệnh bằng limit 0,25 ATR để theo dõi, các phép kiểm còn lại coi như đã xong. Runtime vẫn vào lệnh ngay như cũ. Bộ câu hỏi đổi thành `learning-questions-v4`.

- **Cách tính:** với mỗi tín hiệu short Perp của Setup-2 từ đầu soak, vòng học tính hai kết quả:
  - vào ngay theo luật hiện tại;
  - vào bằng lệnh sell limit cao hơn giá đóng 0,25 ATR, chỉ chờ trong nến H4 kế tiếp, khớp đọc trên M15. Không khớp thì tính 0%.

  Cả hai dùng chung trailing ATR có trần 5% và thoát theo kênh Donchian. Báo cáo cộng dồn chênh lệch theo từng cặp (số cặp, trung bình, sai số chuẩn), và tin Telegram có thêm một dòng tóm tắt.
- **Chạy thử trên dữ liệu VPS** (chỉ đọc, chưa deploy):
  - 4 tín hiệu từ 15/10, khớp cả 4. Có 3 cặp đã đóng, limit hơn trung bình +0,19% (sai số 0,16).
  - Riêng HYPE ngày 15/10: vào ngay thì chạm stop −5%, vào bằng limit thì vẫn đang mở ở −0,7%. Lý do là stop của lệnh limit cao hơn, vì giá khớp cao hơn.

## 2026-10-19 — Kiểm cách vào lệnh của Setup-2; đăng ký thiết kế nghiên cứu Setup-3

Nguyên nhân: tín hiệu soak ZEC Perp short lúc 20Z ngày 18/10 vào ở đáy kênh rồi chạm stop sau khoảng 2 giờ (−5,2% nếu là lệnh thật). Các phép kiểm dưới đây chỉ đọc dữ liệu VPS, script nằm ngoài repo, và runtime không đổi.

- **Setup-2 short Perp gần như hoà sau phí.** Danh mục slot giống runtime, từ 4/2024 tới nay: tổng +39,3%, trong đó Perp +10,5% và Spot +28,9%; mức sụt giảm tối đa 7,8%.
- **Không có tác dụng:**
  - lọc theo khoảng cách tới EMA50 hay EMA200 lúc có tín hiệu;
  - chờ một nến xác nhận, cả 4 kiểu đã thử: tổng còn +16% đến +35%. Cách này loại được 60% lệnh thua, nhưng cũng loại 45% lệnh thắng, và những lệnh còn lại thì vào muộn.
- **Có dấu hiệu nhưng chưa chắc:** short Perp vào bằng lệnh limit cao hơn giá đóng 0,25 ATR, chờ 1 nến. Tổng lên +43,5%, sụt giảm tối đa 5,8%; kết quả không đổi khi kiểm thứ tự khớp lệnh trên M15. Tuy vậy t chỉ khoảng 1,8. Với long Spot, cách này không có lợi. Chưa đổi gì; quyết định sau G6.
- **Thiết kế nghiên cứu Setup-3** (bán khi giá hồi lên EMA200 hoặc kênh trên trong xu hướng giảm, kèm luật giao cắt EMA50/200 rồi chờ giá hồi về EMA200 theo bài Binance Square Julius gửi): luật, chia giai đoạn dò và kiểm, tiêu chí đạt và dự đoán đều được ghi trước khi chạy. Xem [setup3-ema200-retest-research.md](docs/setup3-ema200-retest-research.md).
- **Kết quả Setup-3: không chiều nào đạt.** Code ở `intraday/replay_v2/setup3_study.py`, dữ liệu xuất bằng `scripts/setup3_export.py`; giai đoạn kiểm chạy đúng một lần với hash dữ liệu và cấu hình khớp lúc dò.
  - **Short Perp ở vùng EMA200:** giai đoạn dò +0,85% mỗi lệnh (t 1,76), nhưng năm 2026 −0,10%. Khi bỏ ZEC từ tháng 10 thì còn −0,47%. Quy luật Julius thấy chỉ đúng trong tháng 10, không đúng cho cả năm.
  - **Chạm kênh trên** cho kết quả âm. **Long Spot** âm ở cả 6 biến thể.
  - **Luật trong bài Binance Square:** quá ít lệnh, chỉ 9–10 lệnh mỗi chiều trong năm 2026, và không dương ở cả hai giai đoạn.
  - Không đề xuất soak.

## 2026-10-18 — Deploy `7b815f4` lên VPS (vòng học hằng tuần)

Julius duyệt deploy. Làm theo [runbook](docs/runbooks/deploy-a88825b.md), từ `d517251` lên `7b815f4`. Schema, active set và `.env.intraday` không đổi. Job học hằng tuần **chưa** được lên lịch, và journal Demo **chưa** được mount: cả hai chờ sau G6, mỗi việc Julius duyệt riêng.

- **Trước deploy (11:45Z):**
  - `doctor` ok; chế độ paper, `execution_enabled: false`, schema 23.
  - Worker chạy từ 03:08Z, `RestartCount` 0.
  - Image cũ được gắn tag `rollback-d517251-20261018`: worker `7cc1093919cc`, web `3cbae178177b`, admin `00a19cffbb4b`.
- **Sao lưu:** `intraday-20261018T122020484871Z.sqlite3`, 2,76 GB, SHA-256 `043e0cb7…dc54512c`. Lệnh verify trả `status` ok và `integrity` ok.
- **Deploy:**
  - Build: worker `62bcab6bfd1d`, web `7be8e6f913d2`, admin `40534650d642`.
  - Restart sau khi đã ghi nến 12Z: web lên lúc 12:24Z (healthz 200), sau đó worker lúc 12:24:39Z.
- **Sau deploy:**
  - `doctor` ok, không có job lỗi.
  - Vẫn chế độ paper, `execution_enabled: false`; active set `c3d57eda…` seq 1.
  - Trong image admin đã có lệnh `aigt learning review|proposals`.
  - Log worker không có lỗi.
  - Heartbeat: 9 route có nến tới 12Z, các tỉ lệ giữ như trước deploy. BTC Spot và HYPE Spot vẫn 0%, đây là lỗi đã biết.
- **Lùi lại nếu cần:** `docker tag intraday-<svc>:rollback-d517251-20261018 intraday-<svc>:latest`, rồi `git checkout --detach d517251`. Khởi động lại web trước, worker sau.
- **Còn kiểm:** lúc đổi ngày 00:00Z ngày 19/10, worker phải có `RestartCount` 0 (tính từ lần khởi động 12:24Z) và đủ 9 route; job dọn dữ liệu phải chạy lúc 02:00Z.

## 2026-10-18 — Phần A chuyển sang theo dõi điểm khung giờ; bỏ đề xuất phiên và giai đoạn 2

Julius chốt ba bước: tính điểm khung giờ cho mọi tín hiệu Setup-2 nhưng không cho ảnh hưởng tới việc vào lệnh; phần A chuyển sang theo dõi; chỉ đưa điểm vào runtime khi dữ liệu đã chứng minh. Bộ câu hỏi đổi thành `learning-questions-v3`.

- **Ba phép kiểm chỉ đọc dữ liệu, chạy trên VPS, dẫn tới quyết định này:**
  - **Lệnh 1 giờ theo khung giờ** (từ 3/2024 và riêng năm 2026): không cửa sổ nào (30, 60, 90 ngày) cho lãi sau phí 0,20%. Cấu hình tốt nhất là 60 ngày với t ≥ 3: tuần sau +0,09% đến +0,10% mỗi lệnh, thắng 55%. Cửa sổ 30 ngày không giữ được quy luật sang tuần sau.
  - **Hoãn vào lệnh Setup-2** (340 lệnh replay luật thuần): hoãn 1 giờ trung bình −0,13%, hoãn 8 giờ −0,44%. Tín hiệu ra lúc 16h và 20h UTC thiệt nhiều nhất (−0,3% đến −0,4%). Không giờ nào có quy tắc hoãn đạt t ≥ 2 trên dữ liệu trước 2026. Runtime vào lệnh ngay sau khi nến H4 đóng là đúng.
  - **Điểm khung giờ của lệnh Setup-2**, cửa sổ 60 ngày, tính trên 4 giờ sau lúc vào:
    - lệnh cùng chiều khung giờ có lãi hơn ở cả hai giai đoạn: trước 2026 +1,75% so với −0,76%; năm 2026 +2,32% so với +0,55%;
    - nhưng năm 2026 chưa phân biệt được với may rủi, tương quan chỉ khoảng +0,05, và cửa sổ 90 ngày cho kết quả ngược lại trong năm 2026;
    - 11% số tín hiệu ra khi slot đã đầy, 26% ra cùng nến với một tín hiệu khác.
- **`_hour_scores`:** mọi tín hiệu Setup-2 (`entry=true`) từ đầu soak đều có điểm. Điểm = trung bình lợi nhuận H1 của 4 giờ sau tín hiệu, tính trên 60 ngày **trước** tín hiệu, nhân với chiều của lệnh.
  - Kết quả mỗi tín hiệu là mô phỏng bằng luật thoát của chính rule. Nếu tín hiệu đã thành lệnh Demo thì kèm thêm lãi ròng thật.
  - `hourscore:summary` cộng dồn từ đầu, so nhóm cùng chiều với nhóm ngược chiều.
  - Điểm do vòng học tự tính lại từ nến, nên runtime và worker không phải sửa gì.
- **Thay đổi trong bộ câu hỏi:**
  - bỏ A3 (đề xuất phiên), bỏ các điều kiện lọc đề xuất và mục `screened`;
  - A3 mới là nhận xét về điểm khung giờ, không có đề xuất;
  - thống kê khung giờ chuyển sang cửa sổ 7 và 60 ngày;
  - tin Telegram có thêm một dòng điểm khung giờ.
- **Phần E: khoá đề xuất đổi thông số Jev** (Julius chốt):
  - Lý do: chưa bộ câu hỏi Jev nào đoán tốt hơn tỉ lệ nền. Với câu hỏi cũ, Brier là 0,28–0,30, trong khi đoán theo tỉ lệ nền là 0,24–0,25. Với bộ v2 trên lịch sử Setup-2, Brier là 0,32–0,46, trong khi đoán theo tỉ lệ nền là 0,23. Vì vậy chỉnh ngưỡng nào cũng không làm câu trả lời có ích hơn.
  - Cơ chế: `JEV_PARAMETER_PROPOSALS = False`. Code bỏ các đề xuất `parameter` trước khi kiểm, ghi chúng vào `screened`, và giữ phần còn lại của review. Tin Telegram báo số đề xuất bị bỏ.
  - E2 chỉ còn đề xuất sửa câu hỏi khi có kiểu sai lặp lại.
  - Phần kiểm khoảng giá trị thông số vẫn giữ trong code, để mở lại khi có bộ câu hỏi đạt.
- **Giai đoạn 2 cũ** (Demo chạy lệnh theo phiên với túi vốn riêng) được bỏ. Khi đủ khoảng 100 tín hiệu mới mà lệnh cùng chiều vẫn tốt hơn rõ rệt, sẽ viết kế hoạch riêng để dùng điểm khi hai tín hiệu tranh một slot hoặc để giảm khối lượng lệnh ngược chiều.
- **Chạy thử trên VPS** (proxy Opus 4.6, 85 giây): review hợp lệ. Cả 3 tín hiệu short ngày 15/10 đều ngược chiều khung giờ và đều chạm stop trong mô phỏng (−4,9%). Mẫu này còn quá nhỏ để kết luận.

## 2026-10-18 — Vòng học: phần A phân tích theo nến H1, thêm số đo đảo chiều

Julius chốt: phần A dùng nến H1, kèm hai số đo đảo chiều. Bộ câu hỏi đổi thành `learning-questions-v2`.

- **Nến H1** được ghép từ nến M15 Perp; giờ nào thiếu quý thì bỏ. Mỗi coin có 24 khung giờ, tính trên 7 ngày và 90 ngày:
  - trung bình, sai số chuẩn, `t_stat`, tỉ lệ nến tăng, biên độ;
  - `reversal_share` và `reversal_z`: nến đi ngược chiều với 4 giờ trước nó bao nhiêu % số lần, so với mức 50%;
  - `after_up` và `after_down`: trung bình của nến sau 4 giờ tăng và sau 4 giờ giảm;
  - riêng cửa sổ 90 ngày có `halves_agree`: hai nửa 45 ngày có cùng chiều với cả cửa sổ không.
- **Đề xuất theo phiên (A3)** là lệnh giữ đúng 1 giờ, kèm điều kiện `always`, `after_up` hoặc `after_down`.
  - Code loại riêng đề xuất nếu: `|t_stat|` dưới 3; hai nửa 45 ngày không cùng chiều; đề xuất đi ngược chiều trung bình; hoặc trung bình không vượt phí khứ hồi (Perp 0,20%, Spot 0,30%, như trong replay).
  - Đề xuất bị loại được ghi lý do vào `screened` và không làm hỏng các phần còn lại của review.
  - Ngưỡng là 3 chứ không phải 2 vì mỗi tuần quét khoảng 360 số đo (24 giờ × 5 coin × 3 điều kiện), nên ngẫu nhiên cũng cho vài số vượt 2.
- **Chạy thử trên dữ liệu VPS** (proxy Opus 4.6, 76 giây):
  - không số đo nào trong 360 số đo 90 ngày có `|t|` ≥ 3;
  - 12 số đo có `|t|` ≥ 2, ít hơn khoảng 16 mà ngẫu nhiên đã kỳ vọng;
  - LLM đưa 3 đề xuất phiên, code loại cả 3, review vẫn hợp lệ;
  - dữ liệu gửi cho LLM tăng từ khoảng 104 KB lên 210 KB.

## 2026-10-18 — Chạy thử vòng học có LLM; đọc được JSON bọc trong khối code

- **Model free của OpenCode Zen không dùng được:** `glm-5.3-flash-free` trả `403 FreeTierError`, vì gói free chỉ cho gọi từ trong ứng dụng OpenCode. Profile `llm-opencode-glm53flash` vẫn còn trên VPS nhưng không dùng.
- **Profile `llm-proxy-opus46`:**
  - model `ag/claude-opus-4-6-thinking`, gọi qua proxy trên VPS tại `http://host.docker.internal:20128/v1`;
  - chỉ lưu profile, không gán role LLM.
- **Lỗi `invalid_json`:** proxy bỏ qua `response_format` và bọc JSON trong ```` ```json ```` ````. `StructuredLLMClient` giờ bỏ khối bọc đó trước khi đọc JSON. Bước kiểm theo schema Pydantic và bước kiểm của vòng học vẫn giữ nguyên. Có test mới trong `tests/test_intraday_llm_pipeline.py`.
- **Kết quả chạy thử** (tuần 11–18/10, chưa deploy, nạp code tạm vào container admin):
  - câu trả lời hợp lệ, có 7 đề xuất;
  - 88 giây, khoảng 51 nghìn token vào và 5,4 nghìn token ra;
  - báo cáo nằm ở `replay-reports/learning-trial/lr-20261018-083332.json`, tách khỏi sổ đề xuất thật.
- **Nhận xét về chất lượng:**
  - Đề xuất theo phiên yếu. Trong 30 khung giờ 90 ngày, chỉ 2 khung có trung bình lớn hơn 2 lần sai số chuẩn (ZEC 04h, HYPE 04h), trong khi ngẫu nhiên đã kỳ vọng khoảng 1,5 khung. Đề xuất short HYPE 12h dựa trên trung bình 90 ngày chỉ bằng khoảng 0,55 lần sai số chuẩn.
  - Phần E vẫn đưa 2 đề xuất dù tuần này không có lần Jev nào sai mà tự tin. Một đề xuất giữ nguyên giá trị; đề xuất kia hạ ngưỡng ZEC từ 0,85 xuống 0,75 mà không tránh được lần sai nào.

## 2026-10-18 — Vòng học hằng tuần do LLM xử lý; bỏ tiêu chí 30 lệnh

Julius chốt: LLM xử lý vòng học mỗi tuần; đề xuất theo phiên được thử thẳng trên Demo sau khi Julius duyệt, không qua replay; **bỏ tiêu chí 30 lệnh** để tắt Jev (mục ngay dưới đây không còn hiệu lực).

- **Nguyên tắc:** code tính mọi con số, LLM đọc và đề xuất, Julius duyệt. Không có gì tự áp dụng.
- **`intraday/learning_review.py`, hàm `collect_week`** (tất định, chỉ đọc) thu các nhóm dữ liệu sau:
  - **Chart H4 7 ngày** của các route: lãi lỗ tuần, biên độ, ADX, vị trí so với EMA; kèm lãi tuần của BTC để so.
  - **Thống kê theo khung giờ H4** cho từng coin, trên 7 ngày và 90 ngày: lợi nhuận trung bình, tỉ lệ nến tăng, biên độ, số mẫu và sai số chuẩn.
  - **Lệnh Demo**, dựng lại từ journal theo cách cảnh báo Telegram đang ghép lệnh: lãi ròng sau phí, trượt giá, MFE/MAE, phần lãi trả lại, khoảng cách stop, funding ước tính, ý kiến Jev, tin 48 giờ **trước** lúc vào lệnh.
  - **Lệnh bị bộ lọc chặn hoặc không thành lệnh:** mô phỏng kết quả "nếu đã vào" bằng trailing ATR và luật thoát Donchian của chính rule, tổng hợp theo từng bộ lọc.
  - **Jev:** chọn các lần độ tin ≥ 0,8 mà sai so với nến H4 kế tiếp, tính Brier, gắn nhãn nguồn `setup2` hay `legacy`; kèm các thông số Jev của từng rule và khoảng cho phép lấy từ hợp đồng.
  - **Rủi ro tuần tới:** tương quan 30 ngày, funding và OI, tin đã xác minh. Hệ thống chưa có lịch unlock token; báo cáo ghi rõ giới hạn này.
  - **Đề xuất cũ** đã được duyệt.
  - **Vận hành:** heartbeat, lỗi Jev, tin Telegram chưa gửi, sự kiện an toàn của Demo, chi phí model. Phần này chỉ để code tính, không gửi cho LLM.
- **Bộ câu hỏi `learning-questions-v1`:**
  - Gồm A (chart, khung giờ, đề xuất theo phiên), B (lệnh), C (thoát lệnh), D (bị lọc), E (Jev và đề xuất sửa câu hỏi hoặc thông số), F (tuần tới), G (đề xuất cũ).
  - Mỗi tuần một lần gọi, JSON schema chặt, `max_tokens` 12.000.
  - Code từ chối câu trả lời nếu: trích id hay khoá không có trong dữ liệu; đề xuất short Spot hoặc giờ không phải nến H4; thiếu bằng chứng 90 ngày; thông số Jev ngoài khoảng cho phép; dùng loại "sự kiện trước lệnh" mà không có tin trước giờ vào.
  - Vòng học dùng profile LLM riêng (`--profile`), không gán role LLM, để analyst hằng giờ không chạy lại.
- **CLI:**
  - `aigt learning review [--no-llm] [--profile ID] [--execution-database PATH] [--notify]` ghi báo cáo JSON vào `<report dir>/learning/`.
  - `aigt learning proposals list|approve|reject` quản lý sổ đề xuất.
- `StructuredLLMClient.complete` nhận thêm `max_tokens` (mặc định vẫn 4096).
- **Chạy thử `collect_week` trên database VPS** (chỉ đọc, chưa deploy), ra 104 KB dữ liệu cho LLM sau 1,6 giây:
  - Cả 3 tín hiệu short Perp ngày 15/10 nếu vào lệnh đều đã chạm stop −5% (mô phỏng).
  - 3 lần NEAR Perp bị bộ lọc chặn, nếu vào cũng chạm stop.
  - 23 lần gọi Jev trong tuần, trong đó 17 lần là của engine BTC cũ; không có lần nào tự tin ≥ 0,8.
  - LLM analyst còn chạy tới 08:02Z ngày 16/10 (413 lần gọi, 2,84 USD), sau đó tắt.
- **Chưa làm:**
  - chạy thử với LLM (cần Julius chọn profile);
  - lên lịch job hằng tuần và mount chỉ đọc journal Demo vào container admin hoặc worker, sau G6, Julius duyệt riêng;
  - giai đoạn 2: Demo chạy các đề xuất theo phiên.
- Sơ đồ version 8: https://claude.ai/artifact/T1iqibi77GXfDccfjLPci8

## 2026-10-18 — Chốt tiêu chí tắt Jev; giữ các phần không dùng tới sau G6

Julius chốt hai việc. Tiêu chí được chốt trước khi có dữ liệu Demo, như cách đã làm với nhánh B.

- **Tiêu chí tắt Jev:**
  - **Mẫu:** 30 lệnh Demo đầu tiên đã đóng mà có ý kiến Jev, tức trường `entry_jev` mà `multi_runtime.py` ghi khi vào lệnh ở chế độ tư vấn. Bỏ các lệnh có `jev_unavailable`.
  - **Đo:** lãi ròng trung bình mỗi lệnh, tính bằng % sau phí. So nhóm Jev phản đối (`would_allow = false`) với nhóm Jev đồng ý.
  - **Tắt Jev** nếu nhóm phản đối có lãi trung bình bằng hoặc cao hơn nhóm đồng ý, hoặc nếu một nhóm có dưới 5 lệnh (Jev gần như luôn trả lời một kiểu nên không mang thông tin).
  - **Giữ Jev ở chế độ tư vấn** nếu nhóm phản đối có lãi trung bình thấp hơn, và xét lại ở mốc 60 lệnh. Chỉ bàn tới giai đoạn 2 (phủ quyết) nếu ở mốc 60 lệnh nhóm phản đối vẫn thấp hơn.
  - Không đổi câu hỏi Jev trong lúc đang gom mẫu. Nếu đổi câu hỏi, mẫu đếm lại từ 0.
- **Các phần không dùng** (rule generator, 3 analyst và thesis, engine BTC paper cũ, cờ `news_pause`): giữ nguyên tới sau G6, để không đụng code lúc soak và G5/G6. Sau 28/10 lập delete request để Julius duyệt từng phần. News loop vẫn giữ, vì bước 1 nhánh B cần tin.
- Sơ đồ đã cập nhật lên version 7: thêm tiêu chí trên, ghi rằng LLM đang tắt (`no_active_llm`) và Jev tư vấn đã deploy. https://claude.ai/artifact/T1iqibi77GXfDccfjLPci8

## 2026-10-18 — Deploy `d517251` lên VPS

Julius duyệt push và deploy. Làm theo [runbook](docs/runbooks/deploy-a88825b.md), từ `ffd67c7` lên `d517251`. Schema, active set và `.env.intraday` không đổi.

- **Trước deploy:** paper, `execution_enabled: false`, schema 23. Image cũ được gắn tag `rollback-ffd67c7-20261018` (worker `85a96fc0de01`, web `50db31d83ae2`, admin `d7c2d1d27b4d`).
- **Sao lưu:** `intraday-20261018T030625936718Z.sqlite3` (2,73 GB, SHA-256 `9027432b…20b9de1`, integrity ok).
- **Deploy:** build worker `7cc1093919cc`, web `3cbae178177b`, admin `00a19cffbb4b`; image worker đã có `busy_timeout = 30000`. Web lên lúc 03:08Z (healthz 200), sau đó worker lúc 03:08:50Z.
- **Sau deploy:** `doctor` ok. Chỉ có một job báo lỗi, là `asset_spot_4h_hypeusdt` với `StaleMarketData`, lỗi đã biết. Active set `c3d57eda…` seq 1. Log worker không có lỗi khoá.
- **Lùi lại nếu cần:** `docker tag intraday-<svc>:rollback-ffd67c7-20261018 intraday-<svc>:latest`, `git checkout --detach ffd67c7`, khởi động lại web trước, worker sau.
- **Còn kiểm:** lúc đổi ngày 00:00Z ngày 19/10, worker không được khởi động lại (`RestartCount` 0) và đủ 9 route; job dọn dữ liệu chạy lúc 02:00Z.

## 2026-10-18 — Worker sập lúc 00:00Z vì database khoá; nới thời gian chờ khoá, sửa job dọn dữ liệu

- **Kiểm sau deploy `ffd67c7`:** nến 00:00Z ngày 18/10 ghi đủ 9 route, lúc 00:00:10–00:00:46Z. Hai ngày trước, nến này bị mất. Heartbeat: 7 route 100%, ETH Perp và NEAR Spot 95,7% (mất 1 nến từ trước deploy). Jev gọi 3 lần, không lỗi.
- **Tín hiệu từ 10/10:** chỉ có 3 tín hiệu, đều ở nến 2026-10-15 04:00Z (short Perp ETH, HYPE, ZEC). Cả 3 tin `[SOAK]` gửi Telegram ngay lần đầu. Sau đó luật không ra tín hiệu: Perp vướng volume dưới MA20, Spot dưới EMA200/EMA50.
- **Sự cố:** lúc 00:00:07Z ngày 18/10, vòng chính của worker gặp `sqlite3.OperationalError: database is locked`, khi ghi lịch job `portfolio_soak_perp`. Chỗ này không bắt lỗi, nên tiến trình chết. Docker tự bật lại worker lúc 00:00:07,66Z và không mất nến. Lúc 00:01:07Z, `gate_automation` và luồng `_asset_rule_bootstrap_loop` cũng gặp lỗi khoá. Lỗi có từ trước `ffd67c7`.
- **Chưa xác định được** luồng nào giữ khoá ghi quá 5 giây lúc 00:00:02–00:00:07Z. Database không có dòng nào được ghi trong khoảng đó, nên thủ phạm là một giao dịch không để lại dấu vết.
- **Sửa:**
  - `busy_timeout` của `IntradayStore` tăng từ 5 lên 30 giây. Khi có tranh khoá, các lần ghi chờ thay vì lỗi, nên vòng chính không còn chết, và job Setup-2 không còn rơi vào lỗi `OperationalError`.
  - Hàm `prune_external_observations` từng chạy một lệnh `DELETE` quét toàn bảng (348 nghìn dòng). Lệnh này giữ khoá ghi suốt lúc quét, kể cả khi không xoá dòng nào, và chạy đúng lúc đổi ngày 00:00Z. Giờ hàm xoá theo từng nguồn, dùng index `(source, received_at)`, mỗi lô 2.000 dòng trong giao dịch riêng, và chỉ chạy từ 02:00Z. Bảng này có dữ liệu từ 27/09, nên lệnh xoá thật sẽ bắt đầu từ 27/10, ngay trước G5, với khoảng 19 nghìn dòng mỗi ngày.
- Test: `tests/test_external_context.py` thêm test xoá theo lô và dùng index.

## 2026-10-17 — Chốt hướng nhánh B (LLM): hai bước, hội đồng tranh luận vẫn xây

- Julius đồng ý làm nhánh B sau G6, theo hai bước:
  - **Bước 1, lọc sự kiện.** Mỗi tín hiệu Setup-2 gọi model một lần, đọc tin 48 giờ và funding, OI của đúng coin đó, rồi trả lời: có sự kiện không (unlock, hack, delist, tin pháp lý, sự cố sàn), thuộc loại gì, nặng tới đâu. Kết quả là nhãn kèm lý do, chạy shadow trên Telegram. Cách chấm được chốt trước khi nhìn dữ liệu.
  - **Bước 2, hội đồng tranh luận** (3 analyst, Bull/Bear, Risk/PM). **Vẫn sẽ xây**, sau khi bước 1 có số liệu; khi đó so hai bước trên cùng các tín hiệu.
- Lý do làm bước 1 trước: luật đã đọc phần kỹ thuật, và Jev đọc cùng các con số đó mà không thêm được gì. Phần luật không thấy là sự kiện chưa vào giá. Không backtest được vì LLM đã biết giá quá khứ, nên chỉ chấm được trên tín hiệu mới.
- Sơ đồ Đề xuất đã cập nhật: https://claude.ai/artifact/T1iqibi77GXfDccfjLPci8

## 2026-10-17 — Deploy `ffd67c7` lên VPS

Julius duyệt push và deploy. Làm theo [runbook](docs/runbooks/deploy-a88825b.md), từ `236e767` lên `ffd67c7`: chế độ Jev tư vấn, heartbeat soak không phụ thuộc Jev, chạy lại nến bị lỗi database khoá.

- **Trước deploy:** `doctor` ok, paper, `execution_enabled: false`, schema 23, Jev `jev-openrouter-65b8e694`, LLM tắt; active set `c3d57eda…` seq 1. Image cũ được gắn tag `rollback-236e767-20261017` (worker `baef4e8883b2`, web `2b1d5db7b4a9`, admin `13704e151e50`).
- **Sao lưu:** `~/.local/state/aigorithmic-trading/backups/intraday-20261017T081243388313Z.sqlite3` (2,66 GB, SHA-256 `52765914…314daa08`, `backup verify` ok).
- **Deploy:** checkout `ffd67c7` (detached), build worker/web/admin (worker `85a96fc0de01`, web `50db31d83ae2`, admin `d7c2d1d27b4d`), web lên lúc 08:15Z (healthz 200), sau đó worker lúc 08:16Z.
- **Sau deploy:** `doctor` ok, schema 23, provider và active set không đổi; log worker không có lỗi hay `database is locked`. Heartbeat lúc 08:16Z: cả 9 route đã ghi nến 08:00Z. ETH Perp và NEAR Spot đạt 94,7% (mất 1 nến như đã ghi), 7 route 100%. Jev chưa lỗi lần nào.
- **Lùi lại nếu cần:** `docker tag intraday-<svc>:rollback-236e767-20261017 intraday-<svc>:latest` cho worker, web, admin, `git checkout --detach 236e767`, rồi khởi động lại web trước, worker sau. Database không đổi.
- Còn kiểm: nến 12:00Z (19:00 giờ VN) có đủ 9 route.

## 2026-10-17 — Thử lại nến Setup-2 khi database bị khoá; kiểm heartbeat trước deploy

- Kiểm heartbeat soak lúc 07:58Z bằng `scripts/soak_heartbeat.py` (chỉ đọc). Sau 18 nến H4: 7 route 100%, ETH Perp và NEAR Spot 94,4% (mỗi route thiếu 1 nến). Jev được gọi 3 lần, **chưa lỗi lần nào**. HYPE Spot 0% (đã biết, soak lại sau 29/10). Campaign BTC Spot từ 02/10 là của dòng cũ, không thuộc 9 route.
- Hai nến thiếu không do Jev: NEAR Spot nến 2026-10-16 00:00Z và ETH Perp nến 2026-10-17 00:00Z đều báo `error:OperationalError`. Đây là lỗi SQLite, nhiều khả năng là database bị khoá, vì 00:00Z là lúc các job hằng ngày cùng ghi database. Bộ lập lịch đánh dấu khung giờ là lỗi và không chạy lại, nên mất nến. Từ 14/10 chỉ có 2 lần như vậy.
- Mức chịu đựng: 14 ngày có 84 nến, đạt 95% thì mỗi route mất tối đa 4 nến. Với nhịp lỗi hiện tại, khả năng một route trượt là thấp nhưng không bằng 0.
- Sửa: `claim_cadence`/`claim_scheduler_run` nhận `retry_error_codes`. Job Setup-2 (Spot 4h và Perp) được chạy lại khung giờ đã lỗi `OperationalError` ở vòng lặp kế tiếp, trong 30 phút đầu của khung. Các lỗi khác như `StaleMarketData` của HYPE Spot không được thử lại. Ghi observation theo nến vốn idempotent. Không đổi schema.
- Test: thêm 1 test cho việc thử lại (chỉ `OperationalError`, chỉ trong 30 phút, không đụng khung đã thành công). Toàn bộ: **1812 qua, 1 lỗi migration có sẵn**.

## 2026-10-17 — Soạn sẵn kịch bản deploy `a88825b`

- [Runbook deploy](docs/runbooks/deploy-a88825b.md): thay đổi so với `236e767`, giờ an toàn, các bước ghi trạng thái, backup, build, restart (web trước, worker sau), kiểm tra và lùi lại. Không đổi compose, dependency hay schema, nên lùi lại không cần khôi phục database.
- `scripts/soak_heartbeat.py`: chỉ đọc. In độ phủ heartbeat của mọi campaign soak đang chạy theo cách tính cũ và mới, kèm số nến Jev lỗi. Đã thử trên database giả.
- Chưa chạy trên VPS: Julius đang ở ngoài, không vào được mạng LAN của VPS. Deploy cần Julius duyệt.

## 2026-10-17 — Lỗi Jev không còn làm mất heartbeat hay cảnh báo của soak

- Julius chọn giữ Jev ở chế độ tư vấn (chỉ ghi ý kiến), với điều kiện lỗi Jev không bao giờ làm hỏng soak. Lý do: hai lần đo cho thấy Jev không phân biệt được lệnh tốt và xấu ([hiệu chỉnh](docs/jev-calibration-2026-10-16.md), nhánh A trên `feature/jev-setup2-review`), nhưng ý kiến vẫn được ghi để so với lệnh Demo sau này.
- `setup2_store.heartbeat` đếm mọi nến H4 luật đã đánh giá, kể cả nến có `jev_status` là `provider_error`/`gate_error` (trước đây hai trạng thái này bị loại). Vì dòng observation đã được ghi từ trước, khi code này được deploy trước lần đánh giá soak, các nến lỗi Jev cũ cũng được tính lại.
- `run_setup2_observation` bắt mọi lỗi khi gọi Jev, không riêng `RuntimeError`. Ví dụ `PermissionError` của file secret trước đây không được bắt, nên nến đó mất observation. Lỗi được in một dòng JSON `setup2_jev`.
- Khi Jev lỗi, cảnh báo Telegram của tín hiệu luật vẫn được gửi, với dòng "Jev: không trả lời (lỗi khi gọi), tín hiệu luật không đổi". Trước đây không có cảnh báo nào.
- Demo không đổi: chế độ tư vấn đã bỏ qua lỗi Jev và chỉ chờ tối đa 120 giây cho mỗi nến.
- Test: thêm 3 ca (Jev lỗi `insufficient_credits`, `PermissionError`, `ValueError`), cả ba đỏ trên code cũ. Toàn bộ: **1811 qua, 1 lỗi migration có sẵn** (`test_cli_inheritance_and_readiness_…`, đã ghi ngày 15/10).
- Chưa deploy. Cần Julius duyệt cùng đợt deploy `199f800` trước khi đánh giá soak.

## 2026-10-16 — Tắt analyst LLM chạy mỗi giờ trên VPS

- Analyst chỉ phân tích BTC (`store.latest_snapshot()` mặc định `BTCUSDT`), thesis không đi vào soak, Demo hay Jev, tốn khoảng 0,75 USD/ngày. Julius duyệt tắt; vai trò này sẽ do nhánh B (hội đồng LLM theo tín hiệu) đảm nhận sau.
- 08:06Z (sau nến H4 08:00Z): `aigt provider deactivate llm` qua service `admin`; `doctor` báo `llm: null`, Jev giữ nguyên `jev-openrouter-65b8e694`. Không sửa `.env`, không restart worker; news loop vẫn chạy. Vòng analysis và tự đề xuất rule kiểu cũ bỏ qua khi không có LLM.
- Bật lại: `aigt provider activate llm llm-openai-compatible-3ba62fbf` (profile và key vẫn giữ).

## 2026-10-16 — Đo hiệu chỉnh của Jev và cỡ mẫu tín hiệu lịch sử

- Chỉ đọc database VPS và kết quả G4. Replay G4 có 340 lệnh đóng trên 10 route trong khoảng 30 tháng (2–3 lệnh/tuần).
- 3.880 mẫu Jev (1 mỗi coin mỗi 15 phút, từ 115.164 câu trả lời): Brier của xác suất hướng kém hơn đoán theo tỉ lệ nền ở 1h, 4h và 24h; Jev quá tự tin. Khi RSI < 30, nhịp hồi Jev chọn đúng sau 4h (lên 70%) nhưng mất sau 24h. Chi tiết: [Jev calibration](docs/jev-calibration-2026-10-16.md).

## 2026-10-15 — Key Binance Demo lên VPS (chuẩn bị G5)

- Key Demo cũ (30/9) không đọc được cả Spot lẫn Futures nên `connect` từ chối Clear/Replace; Julius đổi tên file cũ thành `binance-demo.json.old-20260930` (chờ xoá, mục H1 trong delete request) rồi nhập key mới bằng `aigt connect bnb demo`.
- Trên VPS: tạo `state/execution` và `state/execution-secrets` (700, 1000:1000), chép `binance-demo.json` (600, 1000:1000). Container `demo-execution` chưa bật; không configure/activate, không đặt lệnh. Còn chờ: cài tài khoản Demo (One-way, Single-asset, Isolated, ≥ 500 USDT mỗi ví), soak 28/10, deploy `199f800` và G5/G6 duyệt riêng.

## 2026-10-15 — Chốt kiểm chứng Jev advisory

- Toàn bộ trên `80e9dfd`: **1808 qua, 1 lỗi migration có sẵn được cho phép**, 23 cảnh báo. Parity và slot qua; không skip/xfail. Review Standards/Spec không còn finding mở.
- 10/10 ca tham chiếu giữ `result_id` và summary; 99 hash source bảo vệ, payload mặc định và hash candidate giữ nguyên. Báo cáo: [Jev advisory](docs/jev-advisory-verification-2026-10-15.md), có commit/file, sai khác kế hoạch và manifest checksum.
- Chỉ commit local; không đổi VPS, key, active set hay soak; G5/G6 vẫn duyệt riêng.

## 2026-10-15 — Ổn định checksum của fixture A/B

- Toàn bộ test lần xác nhận phát hiện lỗi hash ngẫu nhiên ở `test_compact_ab`, tái hiện cả trên gốc `cad8779`: connection ghi chưa được dọn khiến WAL checkpoint sau lúc chốt checksum, dù dump logic không đổi.
- Chỉ checkpoint/truncate WAL trong fixture trước khi đóng băng input; thêm test dọn connection rồi kiểm hash. Không đổi mã replay hay bỏ/nới assertion bảo toàn checksum.

## 2026-10-15 — Bảo toàn audit advisory khi stop lỗi

- Review độc lập phát hiện nhánh stop bảo vệ thất bại chưa lưu `slot_selection` và làm mất kết quả ứng viên. Lưu audit cùng reservation/failure và trả kết quả Jev cả khi portfolio phải pause, có test Spot/Perp gây lỗi stop.
- Truyền chế độ advisory tường minh vào bước đặt lệnh, không suy chế độ từ sự có mặt của trường ý kiến Jev.

## 2026-10-15 — Quy trình Demo slot dùng Jev tư vấn

- Bổ sung quyết định của Julius vào ADR-007, checklist G5/G6 và Demo runbook: configure với `--jev-mode advisory`, kiểm status và đường lui về `gate` khi paused + flat.
- Giữ nguyên lịch sử diễn tập 15/10; không đổi active set/candidate/soak, không triển khai hay kích hoạt giao dịch trong đợt này.

## 2026-10-15 — Ý kiến Jev trong Telegram

- Tin soak nêu rõ tín hiệu đến từ luật; dòng Jev ghi đồng ý hoặc phản đối, không còn báo không vào lệnh vì Jev.
- Tin Demo đọc ý kiến tại thời điểm đặt lệnh từ reservation bất biến trong journal; giữ nguyên chống gửi trùng và retry khi Telegram lỗi.

## 2026-10-15 — Runtime slot dùng luật và ghi ý kiến Jev theo nến

Nhánh: `feature/jev-advisory`; chỉ làm local.

- Advisory đọc câu trả lời primary đúng route/rule/nến, không đòi provenance hay tuổi 45 giây; thiếu/lỗi thì chờ tối đa 120 giây từ lúc đóng H4 rồi vẫn xếp hạng theo luật. Giữ kiểm champion/soak, giá lệch 1% từ giá đóng H4, spread 0,1%, filters, stop, đối soát và giới hạn danh mục.
- Định danh entry theo `setup2:{rule_id}:{bar}`, bền qua restart và câu trả lời muộn; audit ghi `jev` trong kết quả, từng bước lựa chọn và route khi reserve lệnh. Kiểm lại giá/rủi ro trước khi đóng vị thế để xoay, giá trước submit.
- Hai điểm phụ cần để hỗ trợ đúng đường mới: mở riêng giới hạn `OrderIntent.source_id` từ 128 lên 160 ký tự để chứa rule ID dài tối đa; truyền đúng dấu âm của vị thế short cho coordinator **chỉ trong advisory**. Test nhiều vị thế Perp ban đầu chỉ mở được một do dấu dương bị hiểu là flip, nay mở/xoay được; nhánh gate và per-route cũ giữ nguyên.
- 39 test runtime/source/slot/G5 qua. Hai đối chứng H4 và 8 ca slot 4/5 coin năm 2026 tái chạy giữ nguyên 10 `result_id` và summary; dữ liệu/replay engine không đổi. Không thay tham số/hash rule, active set, heartbeat hay cách lưu `challenger_allowed`.

## 2026-10-15 — Cờ Jev advisory riêng cho allocation Demo slot

Nhánh: `feature/jev-advisory` từ `cad8779`; chỉ làm local.

- Thêm `DemoAllocation.jev_mode` và `configure --jev-mode gate|advisory`; mặc định gate không xuất thêm trường khi serialize, giữ payload cũ từng byte. Advisory chỉ hợp lệ với `setup2_slots_v1`, không nằm trong active set hay tham số rule.
- Cổng advisory chấp nhận thiếu/khác ý kiến Jev nhưng vẫn đòi luật nổ và gọi coordinator rủi ro với cách tính cỡ lệnh cũ; cổng mặc định và profile cũ giữ hành vi. Hàm thuần `jev_advice` dùng cùng logic lý do của cổng cũ.
- Kiểm thử tập trung: 46 test allocation/CLI/cổng và hồi quy cổng cũ, Setup-2 Perp, active set qua. Chưa triển khai hay đổi trạng thái Demo/VPS.

## 2026-10-15 — Chuẩn bị G5/G6 Binance Demo cho runtime slot ADR-007

Nhánh: `prep/g5-demo`, worktree `system-trading-g5-prep` từ `18f15da`. Chỉ chuẩn bị và diễn tập local; VPS chỉ đọc, không push/deploy/kết nối key hay gửi lệnh Demo. Chi tiết, lệnh và bằng chứng: [checklist G5/G6](docs/runbooks/slot-runtime-demo-rollout-checklist.md#chạy-thử-g5-trên-database-tạm-2026-10-15).

- Đối chiếu `--help` và code cho connect, leverage, scan/venue, configure, preflight, activate, run/once, status, pause, flatten. Checklist sửa thứ tự venue → leverage → configure, đường source/journal/key trong container, tách cổng G5 và G6; ghi rõ `--once` có thể đặt lệnh, cách đưa key lên VPS với quyền 0700/0600 và UID:GID 1000:1000. Hai bind directory trên VPS hiện chưa có.
- Đọc ID/hash campaign hiện tại để điền 9 route của đợt đầu; mốc đánh giá chung an toàn 2026-10-28 04:11Z, Perp cần funding snapshot mới. Giữ active set cả 10 route: configure paused được khi HYPE Spot chưa có soak đạt, chỉ activate 9 route; test chứng minh HYPE Spot bị từ chối không làm đổi portfolio đã active 9 route. Không sửa Jev, cách dùng `rule_allows_answers` hay tham số/hash rule.
- Chép backup 2,17 GB và manifest về `~/.local/state/aigorithmic-trading/g5-rehearsal/`, verify integrity và SHA-256 `6d25bf92…caefeaa5`; dựng active set/candidate trên bản sao thứ hai. CLI thật dừng do thiếu key. Với boundary fixture, configure ghi paused, gross/Perp/margin 100%/50%/50%, giới hạn lỗ 3%/15%, cap 166,67 USDT; preflight/activate dùng source bản sao dừng vì chưa có champion, không tạo evaluation giả.
- Scan công khai 9 route (thêm HYPE Spot để kiểm cấu hình đủ), đọc Demo exchangeInfo và tính cỡ lệnh 166,67/66,67 USDT: đều vượt minimum ở multiplier 1/0,40; ETH Perp minimum 20 USDT, các route khác 5. Tại 0,40, NEAR Perp tròn xuống giảm 2,875%, ZEC khoảng 1,1–1,2%. Multiplier không có sàn 0,40 nên vốn/cỡ nhỏ hơn vẫn có thể `rejected_quantity`.
- Xác nhận Compose nhận đúng 3 biến Telegram, lần đầu chỉ ghi BASELINE; kiểm bằng fixture, không gửi Telegram thật. Worker ID và active-set ID trên VPS giữ nguyên; checksum backup và số evaluation của bản sao không đổi sau diễn tập.
- Kiểm thử cuối: **1.748 qua, 1 lỗi có sẵn được cho phép** `test_perp_collection_migration`, 23 cảnh báo deprecation; không skip/xfail. Hai lỗi tìm được (help argparse và thiếu mẫu Hermes) được sửa ở các mục dưới. Còn chờ key/quyền giao dịch, mode/leverage thật, soak đạt, quyết định Jev và G5/G6 của Julius.

## 2026-10-15 — Khôi phục file mẫu cài đặt Hermes trong checkout sạch

Nhánh: `prep/g5-demo`. Phát hiện khi chạy toàn bộ pytest cho chuẩn bị G5.

- Gói `integrations/hermes/trading-ops` thiếu file mẫu cài đặt vì `.gitignore` bỏ mọi `.env.*`; thêm ngoại lệ đúng đường dẫn và mẫu công khai chỉ có URL loopback, hai trường token để trống. Không đọc cấu hình hay token runtime, không cài/bật Hermes trên VPS.
- Test distribution có sẵn tái hiện lỗi thiếu file trên worktree mới; kiểm lại gói cài đặt sau khi bổ sung.

## 2026-10-15 — Sửa trợ giúp CLI cho bước chuẩn bị G5

Nhánh: `prep/g5-demo`, worktree riêng; chưa push hay deploy.

- Sửa `active-set init/switch --help` bị lỗi định dạng do ký tự `%` trong mô tả `--waive-net-return`; không đổi tham số, hash rule hay cổng Jev.
- Kiểm thử hồi quy cho cả hai lệnh: tái hiện lỗi trước khi sửa, kiểm trợ giúp in được sàn −5% sau khi sửa.

## 2026-10-15 — Tín hiệu soak đầu tiên; cổng Jev chặn mọi lệnh Setup-2

Nhánh: `feature/slot-runtime`. Chỉ đọc dữ liệu trên VPS, không sửa code, không deploy.

- **Tín hiệu soak đầu tiên:** nến H4 đóng 2026-10-15 04:00Z, ETH, HYPE, ZEC Perp cùng ra SHORT; bot Telegram gửi cả 3 tin ngay lần đầu. Jev từ chối cả ba (Strong Buy 0,49 / Buy 0,37 / Strong Buy 0,42).
- **Phát hiện:** trên 115.164 câu trả lời Jev `perp_intraday` (25/09–15/10), cổng `perp_setup2_entry` qua 0 lần ở ngưỡng 0,85 và ở sàn 0,69; khi RSI14 < 30, Jev chọn mua 99,4%. Jev nhận snapshot 1h chung, không biết luật đã ra tín hiệu, và trả lời theo kiểu đảo chiều. Nếu giữ nguyên, Demo sẽ không vào lệnh nào. Soak và mốc 2026-10-28 không bị ảnh hưởng. Chi tiết và các lựa chọn: [docs/jev-setup2-confirmation-2026-10-15.md](docs/jev-setup2-confirmation-2026-10-15.md).

## 2026-10-14 — Thông báo Telegram cho tín hiệu soak và lệnh Binance Demo

Nhánh: `feature/slot-runtime`. Julius chọn: báo cả soak và Demo, hiện mức thoát thay cho target, khối lượng soak theo % túi, dùng bot của timer BTC paper.

- **Soak (`intraday/trade_alerts.py`, `setup2_store.run_setup2_observation`):** mỗi lần luật Setup-2 nổ và Jev trả lời, worker xếp một tin vào `notification_outbox` (một tin cho mỗi rule, thị trường và nến): coin, Spot LONG / Perp SHORT, Jev xác nhận hay từ chối, giá vào tham chiếu (giá đóng nến), khối lượng `1/3 túi × min(1; 0,02/ATR%)`, stop ban đầu (ATR14×3, tối đa 5%), mức kênh thoát 10 nến. Soak không đặt lệnh, không theo dõi thoát, và không áp giới hạn 3 vị thế, PnL gate hay xoay vòng.
- **Demo (`execution demo run --multi`):** sau mỗi chu kỳ, runtime đọc journal và gửi một lần cho mỗi sự kiện: vào lệnh (khối lượng khớp, giá trung bình, stop đầu tiên, mức kênh thoát), dời stop, thoát lệnh (lý do: kênh thoát, chạm stop, xoay vòng, giới hạn lỗ...) kèm PnL (Perp: PnL sàn đã trừ phí; Spot: PnL theo giá). Lần chạy đầu chỉ ghi mốc, không gửi lại lịch sử; gửi lỗi thì thử lại tối đa 5 lần. Bảng mới `execution_notifications` trong journal Demo.
- **Setup-2 không có chốt lời cố định:** tin nhắn hiện stop và mức kênh thoát thay cho target.
- **Deploy `236e767` và bật Telegram:** web trước, worker sau; 0 luồng chết. Julius nhờ điền `.env.intraday` trên VPS (sao lưu trước ở `~/.local/state/aigorithmic-trading/backups/env-intraday-20261014T045739Z.bak`): bot `@aigorithmic_trading_bot`, chat riêng của Julius, `TELEGRAM_ENABLED=true`; worker được tạo lại, tin thử đã gửi. Token đã xuất hiện trong hội thoại, nên thu hồi bằng `/revoke` và thay token mới.
- **Cấu hình:** dùng `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` của `.env.intraday`. Service `demo-execution` nhận đúng ba biến này (không nhận cả file env). Lỗi gửi hay lỗi đọc journal không làm dừng worker hay runtime.

## 2026-10-14 — Cổng G4: 10/10 route qua replay gate, soak 14 ngày bắt đầu; luồng nền tự khởi động lại

Nhánh: `feature/slot-runtime`, VPS chạy `fcd6136` (Julius duyệt push, deploy, đóng campaign ETH Spot và bắt đầu soak).

- **G4, replay gate (Julius duyệt):** backfill 10 route, tạo candidate, loại 7 candidate cũ còn mở (`--retire-open`), gắn funding đến 2026-10-14T00:00Z. Kết quả gate v2: 9/10 qua — ETH Perp +5,44%, NEAR Perp +5,03% / Spot +9,38%, SOL Perp +4,54% / Spot +1,53%, ZEC Spot +15,09%, ZEC Perp −1,69% (miễn trừ lãi ròng), HYPE Perp −4,26% (miễn trừ lãi ròng, cách sàn −5% còn 0,74 điểm), HYPE Spot 0 lệnh (miễn trừ số lệnh). Danh sách id trên VPS: `~/.local/state/aigorithmic-trading/adr007/`.
- **ETH Spot bị chặn:** campaign gate v2 cũ `cb13b4cb…` vẫn active dù candidate của nó đã bị loại. `4394683` sửa `--retire-open` để đóng campaign này và thêm `aigt setup2 retire-campaign` cho campaign mồ côi.
- **Deploy `4394683`:** web healthy, `doctor` ok, active set giữ nguyên. Sau khi worker lên, `_asset_rule_bootstrap_loop` vẫn chết vì `database is locked` ở một lần ghi thường (`claim_scheduler_run`), không phải ở khởi tạo schema. Đã khởi động lại riêng worker: 0 luồng chết.
- **ETH Spot:** Julius chạy `aigt setup2 retire-campaign --symbol ETH --market spot` (đóng `cb13b4cb…` lúc 04:06Z). Replay lại lần đầu `deferred` vì nến 4h mới nhất cũ 4,1 giờ (ngưỡng 14.700 giây); sau `setup2 backfill` (thêm 1 nến 4h, 16 nến 15m) thì qua: 36 lệnh, +1,98%, DD 5,26%, eval `8053585e…`.
- **Soak 14 ngày:** `start-soak` cho cả 10 route với eval replay đã qua, campaign bắt đầu 2026-10-14 04:09–04:10Z (sớm nhất có thể đánh giá: 2026-10-28 04:10Z). Kết quả từng route lưu ở `~/.local/state/aigorithmic-trading/adr007/soak-*.out` trên VPS.
- **Heartbeat soak:** đếm theo dòng `setup2_observations` của candidate, mỗi nến H4 một dòng, từ `started_at` của campaign (`provider_error`/`gate_error` không tính). 5 route Perp đã ghi nến 04:00Z (trước giờ bắt đầu soak nên chưa tính); 4 route Spot đầu tiên sẽ ghi từ slot 08:00Z.
- **HYPE Spot không ghi được heartbeat:** snapshot Spot 4h cần ít nhất 35 nến đóng cho mỗi khung 4h, 8h, 1d trên Binance Spot; HYPE Spot mới có 20 nến 1d (niêm yết 2026-09-24), nên route báo `error:StaleMarketData` mỗi slot cho tới khoảng 2026-10-29. Vì độ phủ heartbeat tính từ `started_at`, campaign HYPE Spot hiện tại sẽ không đạt 95% và phải bắt đầu lại sau mốc đó (hoặc sửa code). Jev cũng không xác nhận được lệnh Spot HYPE trong khoảng này. **Julius chọn:** không sửa code, bắt đầu lại soak HYPE Spot sau 2026-10-29 (sớm nhất xong khoảng 2026-11-12); các bước ghi trong checklist rollout, giai đoạn 4.
- **Deploy `fcd6136` (bản sửa luồng nền):** web trước, worker sau; `doctor` ok, 0 luồng chết, 0 `database is locked`.
- **Sửa:** mọi loop nền của worker chạy qua `_supervised_loop`; khi loop ném lỗi, worker ghi `{"background_loop": {"loop", "status": "restarting", "code"}}`, chờ 30 giây rồi chạy lại loop thay vì để luồng chết (`fcd6136`).

## 2026-10-14 — Deploy bản sửa khởi tạo database và LLM tự host; cổng G3 đã xong

Nhánh: `feature/slot-runtime`, VPS chạy `35b9aca`.

- **Cổng G3 (2026-10-13 14:00Z, Julius duyệt):** `active-set init` ghi version 1 (`c3d57eda19cd`), profile `setup2_slots_v1`, 5 coin với neo 2024-01-01 (HYPE 2025-06-01), proxy giá HYPE, ngưỡng 400 và hai miễn trừ. Từ 14:01:32Z vòng Jev 30 giây ghi `skipped_inactive` cho mọi coin.
- **LLM tự host:** profile LLM OpenAI/Anthropic-compatible nhận HTTP tới máy chủ cục bộ hoặc nội bộ (`localhost`, IP private, `host.docker.internal`); worker và admin có `host.docker.internal` trỏ về VPS; `aigt connect` in mã lỗi preflight. Container admin gọi được gateway ở `http://host.docker.internal:20128/v1` (401 khi không có key).
- **Sửa lỗi luồng nền chết khi khởi động:** mỗi `IntradayStore` ghi đều chạy lại DDL và migration nên các luồng của worker tranh khoá; nay schema chỉ khởi tạo một lần cho mỗi tệp trong mỗi tiến trình, có thử lại khi tiến trình khác giữ khoá. Sau deploy: 0 luồng chết, 0 `database is locked` ở worker và web (lần deploy trước có 2 đến 3 luồng chết mỗi lần khởi động).
- **Sau deploy:** `doctor` ok, `status` ok (paper, `execution_enabled: false`, schema 23), `/healthz` 200, active set giữ nguyên.
- **LLM đang dùng:** `anthropic/claude-sonnet-5.5` qua OpenRouter (profile `llm-openai-compatible-3ba62fbf`); chu kỳ phân tích đầu tiên sau deploy báo `schema_validation` (đầu ra của model không khớp schema). Không ảnh hưởng luật Setup-2 hay Jev.

## 2026-10-13 — Deploy mã ADR-007 lên VPS (cổng G2)

VPS `julius-vps`, clone `~/AIgorithmic-trading`, Julius duyệt push và deploy. Chỉ deploy mã: **chưa** `active-set init`, chưa soak, chưa đụng Demo.

- **Trước deploy:** VPS chạy `75e1d9e` (nhánh `fix/hyperliquid-frame-checksum`, không có trong `main` hay nhánh này). Đã gộp vào `feature/slot-runtime` (`846e744`, xung đột chỉ ở phần import; 1732 test qua, 1 lỗi có sẵn) để deploy không gỡ mất bản sửa nóng.
- **Trạng thái trước deploy:** worker soak, web healthy, schema v23, BTC paper không vị thế và đang tạm dừng, Demo chưa từng cấu hình.
- **Sao lưu:** `~/.local/state/aigorithmic-trading/backups/intraday-20261013T134644506616Z.sqlite3` (2,17 GB, SHA-256 `6d25bf92…caefeaa5`, `backup verify` ok). Chưa chép ra ngoài VPS.
- **Deploy:** checkout `846e744`, build lại `worker`, `web`, `admin`, rồi `up -d --wait worker web` lúc 2026-10-13 13:53Z. `doctor` ok, `status` ok (paper, `execution_enabled: false`, schema 23), `/healthz` và `/api/active-set` trả 200, active set rỗng (chế độ legacy).
- **Sự cố khi khởi động:** worker và web cùng khởi tạo database nên tranh khoá (`database is locked`). Web tự lên sau 2 lần khởi động lại; nhưng ba luồng nền của worker (`_news_loop`, `_asset_rule_bootstrap_loop`, `_gate_automation_loop`) **chết** vì mở database trước vòng lặp và không thử lại (lỗi có sẵn). Đã khởi động lại riêng worker khi web đã ổn: không còn luồng chết, vòng tin tức chạy lại.
- **Cần sửa sau:** mở database bên trong vòng lặp có thử lại (hoặc đặt busy timeout), và deploy theo thứ tự web trước, worker sau.
- **Ghi nhận:** Jev báo `insufficient_credits` (OpenRouter hết credit), nên mọi lượt soak `perp_intraday` là `provider_error`. Cần nạp credit trước cổng G4.

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
