# Changelog

File này ghi lại các thay đổi đáng chú ý của dự án, bắt đầu từ ngày 2026-10-06. Các thay đổi trước đó xem trong `git log` và các file `docs/*-research.md`.

Mỗi mục gồm ngày, tóm tắt, commit liên quan và link sang tài liệu chi tiết. Mục mới nhất nằm trên cùng.

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
