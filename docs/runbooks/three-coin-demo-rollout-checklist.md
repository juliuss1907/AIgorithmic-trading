# Checklist: đưa runtime 3 coin Setup-2 lên Binance Demo

Trạng thái: **bản nháp chờ Julius duyệt, chưa thực hiện bước nào trên VPS.**
Cơ sở: [ADR-004](../decisions/004-three-coin-system.md), [ADR-005](../decisions/005-eth-near-sol-prospective-paper.md),
[ADR-006](../decisions/006-three-coin-runtime.md), [Demo runbook](../binance-demo-execution-runbook.md),
[weekly gate runbook](../weekly-gate-automation-runbook.md). Mọi lệnh dưới đây đã đối chiếu với CLI
của nhánh `feature/three-coin-runtime`; chỗ nào chưa kiểm chứng được thì ghi rõ ở mục "Chưa kiểm chứng".

## 0. Mục tiêu và phạm vi

- **Mục tiêu:** chạy Setup-2 H4 trên ETH, NEAR, SOL (chia đều 1/3, Spot 60% / Perp short 1x 40%) trên
  **Binance Demo** với vốn Demo 1.000 USDT, để kiểm chứng đường chấp hành: lệnh, stop trên sàn, fill,
  đối soát, funding.
- **Không phải mục tiêu:** chứng minh edge của rule. Verdict đó là paper-test ADR-005 (chạy local,
  checkout riêng `state/research-checkouts/setup2-prospective`, không bị ảnh hưởng bởi việc deploy VPS).
- **Không có giao dịch thật.** REST khoá cứng ở `demo-fapi.binance.com`, không có đường vòng sang mainnet.

### Cấu hình sẽ chạy (chốt trước khi bắt đầu)

| Mục | Giá trị | Nguồn |
|---|---|---|
| Rule | Donchian 30/10 H4, EMA200/50, **ADX14 > 20** và đang tăng, volume > 1,2 × MA20, volume profile 20 ngày, ATR14×3 | `intraday/setup2.py` (hằng số, không phải tham số) |
| Rổ | ETH, NEAR, SOL, mỗi coin 1/3, chế độ `both` | ADR-004/005 |
| Chia vốn | Spot 60% / Perp 40%, Perp isolated 1x | ADR-006 |
| Khác rule nghiên cứu | Jev xác nhận (short cần SELL/STRONG_SELL), stop chặn 10% so với giá vào, sizing không lãi kép, dừng khi lỗ ngày 3% hoặc DD 15% | ADR-006 |
| Jev lỗi/timeout | Luôn thành Hold, tức không vào lệnh mới; thoát vì rủi ro vẫn chạy | README |

### Hệ quả cần chấp nhận trước

- [ ] **`active-set init` kết thúc toàn bộ soak Perp do Jev điều khiển** của 6 coin hiện tại (bằng chứng được giữ).
      BTC và các coin khác chỉ còn thu dữ liệu và ghi `skipped_inactive`. Đây là bước đổi hành vi thật, không phải deploy thuần.
- [ ] Kết quả Demo **không** đọc thẳng thành verdict ADR-005 (bảng khác biệt ở trên).
- [ ] **Đường găng về thời gian là gom đủ lệnh cho replay, không phải soak 14 ngày.** Cửa sổ replay Setup-2 bắt đầu
      từ `anchor + 600 nến H4` = 2026-10-10 (`lifecycle.py`), và gate yêu cầu ≥ 6 lệnh đóng cho **từng route**
      (`gates.py`, `minimum_6_closed_trades`). Với khoảng 1 lệnh mỗi 25 ngày cho mỗi coin và thị trường
      (423 lệnh / 6 route / 1.735 ngày ≈ 1 lệnh mỗi 25 ngày trong backtest), ≥ 6 lệnh mất trung bình khoảng 150 ngày,
      tức **khoảng tháng 3/2027** cho route trung bình; route nhiều tín hiệu hơn sẽ có sớm hơn, route ít hơn thì muộn hơn.
      Từng route kích hoạt riêng được, nên Demo có thể bắt đầu với các route đạt trước.
- [ ] **Không có lợi thế tốc độ khi init sớm.** Số lệnh replay tích luỹ theo lịch từ 2026-10-10 dù `active-set init`
      chạy lúc nào (bar được backfill từ anchor 2026-07-02), nên init sớm chỉ giúp thấy tiến độ từng route và bắt đầu
      heartbeat. Đổi lại là tắt soak Jev cũ ngay. Julius chọn: init sớm (thấy tiến độ hằng tuần) hoặc hoãn init
      (giữ soak cũ) rồi init khi gần mốc. `setup2 backfill` cần active set nên không replay trước khi init được.
- [ ] Runbook không có đường ép vào lệnh. Nếu muốn kiểm chứng đường chấp hành sớm hơn mốc đó, cần một quyết định riêng
      (ví dụ hạ ngưỡng 6 lệnh cho profile Setup-2 qua ADR mới, hoặc một bài test lệnh nhỏ ngoài chiến lược). Hiện không có sẵn.

## Các cổng duyệt (mỗi cổng cần Julius đồng ý riêng)

| Cổng | Hành động | Khi nào |
|---|---|---|
| G1 | Push nhánh `feature/three-coin-runtime` | Trước Giai đoạn 2 |
| G2 | Deploy code lên VPS (worker, web, admin) | Giai đoạn 2 |
| G3 | `active-set init` (đổi hành vi, tắt soak Jev cũ) | Giai đoạn 3 |
| G4 | Bắt đầu soak các route Setup-2 (tiêu credit Jev) | Giai đoạn 4 |
| G5 | `execution demo configure` và `activate` từng route | Giai đoạn 5 |
| G6 | Khởi động container `demo-execution` | Giai đoạn 5 |

## Giai đoạn 1: chuẩn bị, chưa chạm VPS

- [ ] **Kiểm tra commit sẽ deploy.** Nhánh `feature/three-coin-runtime`: đợt A–C đã có trên `origin` đến `d08796e`;
      chỉ còn `d383f8f` (công cụ A/B offline, không đổi runtime) và commit tài liệu này chưa push.
- [ ] Chạy `uv run pytest -q` trên đúng commit. Chấp nhận duy nhất lỗi có sẵn `test_perp_collection_migration`
      (phụ thuộc ngày). Lần gần nhất: 1415 pass, 1 fail.
- [ ] Ghi lại hash commit sẽ deploy: `________`.
- [ ] **Nạp credit OpenRouter** (Jev chỉ được gọi khi setup đã qua đủ filter nên mức tiêu thụ rất nhỏ,
      nhưng hết credit thì mọi quyết định thành Hold). Kiểm tra `aigt provider list`.
- [ ] **Tạo Demo key** trên Binance Demo: quyền Spot và USD-M Futures trading, không cần quyền rút, HMAC.
      Tự chạy trên máy sẽ chạy `demo-execution`: `! uv run aigt connect bnb demo` (nhập ẩn, chỉ GET).
      Không dán key vào chat hay `.env`.
- [ ] Trên Binance Demo, đặt thủ công **One-way, Single-asset, Isolated margin** (preflight chỉ kiểm, không đổi).
- [ ] Dùng tài khoản Demo **chuyên dụng**: không giao dịch tay, không nạp, không reset số dư giữa chiến dịch.

## Giai đoạn 2: sao lưu và deploy (G1, G2)

### 2a. Sao lưu và chụp trạng thái (chỉ đọc)

- [ ] `aigt backup create`, rồi `aigt backup verify ~/.local/state/aigorithmic-trading/backups/intraday-TIMESTAMP.sqlite3`.
- [ ] Copy cả `.sqlite3` và `.manifest.json` sang máy hoặc storage khác.
- [ ] (Tuỳ chọn) `aigt upgrade preflight --backup <file> --output <report.json>` phải exit 0.
- [ ] Ghi lại: commit và image ID đang chạy, container ID và thời điểm start của `worker`, chế độ worker (`soak`),
      rule hash và campaign ID đang active, vị thế đang mở, trạng thái Demo execution (chưa cấu hình / paused).
- [ ] Xác nhận **flat**: không vị thế BTC paper, không vị thế `perp_intraday`, Demo paused và flat (hoặc chưa từng cấu hình).

### 2b. Deploy (G1, G2)

- [ ] Julius duyệt push proposal cho nhánh (G1).
- [ ] Julius duyệt deploy (G2). ADR-006 yêu cầu duyệt riêng cho từng đợt A, B, C. Hai cách, chọn một:
      **(i)** deploy từng commit đợt (`27eaec6` → `844f437` → `d08796e`); **(ii)** deploy HEAD một lần với
      sự đồng ý rõ ràng cho cả ba đợt. Cách (ii) an toàn về hành vi vì **chưa có version active-set thì hệ thống chạy y như cũ**.
- [ ] Chỉ rebuild `worker` và `web` (và `admin` khi cần). **Không** đụng `demo-execution`.
- [ ] Không đổi schema: vẫn v23, trạng thái mới nằm trong bảng mở rộng.

### 2c. Kiểm tra sau deploy

- [ ] `aigt doctor` và `aigt status` đều ổn; `web` healthcheck xanh.
- [ ] `aigt active-set show` báo chưa có version (`current` rỗng).
- [ ] Worker vẫn thu dữ liệu, các soak Jev cũ vẫn chạy như trước.
- [ ] `/api/active-set` trả về; `/api/portfolio` và `/assets` hiển thị bình thường.

## Giai đoạn 3: kích hoạt tập coin (G3, đổi hành vi)

Điều kiện: Demo paused và flat (hoặc chưa từng cấu hình), BTC paper paused và flat, `perp_intraday` flat.

- [ ] Julius duyệt G3.
- [ ] ```bash
      aigt active-set init --coins ETH,NEAR,SOL --mode both --split 60/40 \
        --actor julius --reason "ADR-006 rollout" [--no-demo-portfolio]
      ```
      `--no-demo-portfolio` chỉ dùng khi Demo chưa từng được cấu hình (đường mặc định của journal
      là đường trong container; thiếu journal thì lệnh từ chối nếu không có cờ này).
- [ ] `aigt active-set show`: version 1, ba coin, trọng số 1/3, mốc chỉ báo `2026-07-02` (= freeze ADR-005 trừ 600 nến H4).
- [ ] Xác nhận: vòng Jev 30 giây tắt cho mọi coin; BTC và coin khác ghi `skipped_inactive` mỗi slot H4.

## Giai đoạn 4: dữ liệu, rule, gate v2 (G4)

Lặp cho từng `COIN` trong ETH, NEAR, SOL và từng `MARKET` trong spot, perp (6 route):

- [ ] `aigt setup2 backfill --symbol COIN --market MARKET` (cần 600 nến H4 liền mạch, thiếu thì nhãn `history_not_ready`).
- [ ] `aigt setup2 evaluate --symbol COIN --market MARKET` (chỉ đọc): chỉ báo hợp lý, không báo lỗi dữ liệu.
- [ ] `aigt setup2 candidate --symbol COIN --market MARKET --actor julius --reason "ADR-006" [--retire-open]`
      (ghi lại `rule_id` trả về; `--retire-open` loại candidate legacy đang mở).
- [ ] Replay v2: `aigt assets rules replay CANDIDATE_ID --engine v2`. Cửa sổ bắt đầu 2026-10-10; cần ≥ 6 lệnh đóng,
      lãi ròng > 0, DD < 15%, ≥ 600 nến H4 từ anchor, phủ nến ≥ 99%, nến mới nhất không quá 14.700 giây.
      Chưa đủ 6 lệnh thì trạng thái là `deferred` (`minimum_6_closed_trades`), không phải lỗi: chạy lại hằng tuần.
      Với Perp thêm điều kiện funding đủ.
- [ ] Julius duyệt G4, rồi `aigt assets rules start-soak CANDIDATE_ID [--evaluation-id REPLAY_EVALUATION_ID]`.
- [ ] Soak cần ≥ 14 ngày và heartbeat ≥ 95% (không cần 6 setup đã trưởng thành). Soak chạy được song song với việc
      chờ replay đủ lệnh nên **không phải** đường găng.
- [ ] `aigt assets rules evaluate CANDIDATE_ID`, ghi `SOAK_EVALUATION_ID`.
- [ ] `aigt assets rules activate CANDIDATE_ID --evaluation-id SOAK_EVALUATION_ID` (chỉ chọn champion, không bật fill).
- [ ] Gate tuần tự động **không** lên lịch cho route Setup-2; operator tự chạy các bước trên (ADR-006).

## Giai đoạn 5: cấu hình và kích hoạt Demo (G5, G6)

### 5a. Chuẩn bị máy chạy `demo-execution`

- [ ] Thư mục journal (`AIGT_EXECUTION_HOST_DIR`, mặc định `state/execution`) và thư mục key
      (`AIGT_EXECUTION_SECRETS_HOST_DIR`, mặc định `state/execution-secrets`) **đã tồn tại**,
      mode 700, thuộc `AIGT_EXECUTION_UID/GID` (mặc định 1000/1000). File key mode 600. Compose không tự tạo.
- [ ] Journal riêng cho một tài khoản, một runner, không dịch vụ trùng lặp.
- [ ] DB nguồn đọc được bởi UID đó; **không** đổi owner volume soak một cách mù quáng.

### 5b. Leverage và venue

- [ ] Đặt leverage **1x** cho từng cặp Perp: `aigt perp ETH -leverage` (và NEAR, SOL), nhập 1, Confirm.
      Mặc định là 3x; short Setup-2 cần Isolated 1x. Lệnh chỉ chạy khi paused, flat, reconciled, và đây
      cũng là bước acceptance "ghi leverage + đọc lại" còn nợ trong roadmap.
- [ ] Chọn venue Demo cho từng route: `aigt assets venue set COIN --market spot|perp --venue bnb --environment demo`.

### 5c. Cấu hình và kích hoạt (G5)

- [ ] `aigt execution demo configure --from-active-set --capital 1000 --source-database state/intraday/intraday.sqlite3`
      (không kèm trọng số tay; cấu hình để **paused**). Kiểm giới hạn: gross 100%, Perp 40%, margin 40% ở 1x,
      daily loss 3%, DD 15%.
- [ ] Preflight từng route (chỉ GET): `aigt execution demo preflight --multi --symbol COIN --market MARKET --source-database ...`
      phải `clean_account: true`, lệch đồng hồ trong ngưỡng, không vị thế hoặc lệnh mở.
- [ ] Julius duyệt G5, rồi kích hoạt từng route bằng đúng soak evaluation của nó:
      `aigt execution demo activate --multi --symbol COIN --market MARKET --source-database ... --evaluation-id SOAK_EVALUATION_ID`.
      Kích hoạt không gửi lệnh.

### 5d. Khởi động runner (G6)

- [ ] Julius duyệt G6.
- [ ] Chỉ khởi động `demo-execution` với profile `demo` (`--no-deps`, không kèm `worker`). Kiểm container ID
      và thời điểm start của `worker` **không đổi** trước và sau.
- [ ] `restart: "no"` là chủ ý (acceptance có giám sát). Chạy `aigt execution demo run --multi --once` trước nếu muốn một vòng thử.

## Giai đoạn 6: acceptance có giám sát

Bằng chứng cần thu (chưa từng có credentialed acceptance, xem roadmap):

- [ ] Một lệnh vào đủ điều kiện → **stop gốc trên sàn được xác nhận** (algoOrder).
- [ ] Trailing stop được thay theo thứ tự **đặt stop mới rồi mới hủy stop cũ**, chỉ siết lại.
- [ ] Đóng lệnh reduce-only → fill thật, phí và hoa hồng đúng đơn vị, funding ghi nhận.
- [ ] Đối soát ví với fill và funding khớp; không còn lệnh treo (thường và algo) trên sàn.
- [ ] `aigt execution demo status`: `last_sync_at` mới (không phải làm mới trực tiếp từ sàn).
- [ ] Thử **pause** và **flatten** có chủ đích một lần (Ctrl-C **không** flatten): `aigt execution demo pause --multi`,
      `aigt execution demo flatten --multi`, rồi xác nhận flat bằng `status` và Binance Demo.
- [ ] Nếu có lệnh Perp: xác nhận isolated 1x và protection reduce-only được giữ khi đóng.

## Giai đoạn 7: vận hành hằng ngày và điều kiện dừng

Kiểm hằng ngày: `aigt execution demo status` (last_sync, drift), heartbeat H4, số dư credit OpenRouter,
Telegram paper-test hằng tuần (thứ Hai 12:00 giờ Việt Nam).

**Dừng ngay (pause, rồi flatten nếu cần) khi:**

- [ ] Có lệnh `UNKNOWN`, stop thất bại hoặc không xác nhận được việc hủy stop.
- [ ] Báo drift (leverage, margin mode, số dư đổi ngoài chiến lược), đối soát lệch.
- [ ] Chạm daily loss 3% hoặc DD 15% (runtime tự dừng vào lệnh mới; kiểm tra thủ công vẫn cần).
- [ ] Binance Demo lỗi kéo dài hoặc credit Jev cạn.

**Rollback (không xoá bằng chứng, không restore DB):**

1. `aigt execution demo pause --multi`, rồi `flatten --multi`, xác nhận flat.
2. Dừng **chỉ** `demo-execution`; giữ journal, key và dữ liệu soak.
3. Muốn quay lại hành vi cũ: deploy lại commit trước đó. Schema vẫn v23 nên code cũ đọc được DB,
   bỏ qua bảng mở rộng. Lưu ý code cũ sẽ chạy lại Jev `perp_intraday` cho 6 coin (khoảng 1 USD/ngày).
4. Version active-set chỉ ghi thêm, không xoá được; muốn đổi coin phải paused và flat.
5. Không bao giờ xoá hay ghi đè journal để "sửa" sai lệch; không xoay key khi còn vị thế.

## Giai đoạn 8: ghi nhận

- [ ] Thêm mục vào `CHANGELOG.md` (hash đã deploy, ngày, kết quả acceptance).
- [ ] Cập nhật `docs/binance-demo-execution-runbook.md` và `tasks/roadmap.md` cho các hạng mục acceptance đã xong.
- [ ] Nếu đổi rule (ví dụ ADX hoặc bỏ cap 10%): ADR mới, không sửa ADR-005 hay rule hash.

## Chưa kiểm chứng (cần xác nhận trước khi chạy)

- Commit và cách deploy hiện tại của VPS (`aigt` đã đăng ký hay compose trực tiếp), và VPS đã ở schema v23 chưa.
- UID/GID và quyền của thư mục execution trên VPS; image `demo-execution` có build ở đó không.
- `aigt perp NEAR -leverage` được Binance Demo hỗ trợ cho cả ba cặp.
- Thời điểm thật mỗi route đủ 6 lệnh đóng trong replay v2 (ước lượng 150 ngày là trung bình, phân tán rộng
  giữa 6 route); và replay có tính lệnh đang mở đóng ở cuối cửa sổ hay không.
- Soak có thể bắt đầu trước khi replay pass hay không (README ghi `start-soak` nhận `--evaluation-id` tuỳ chọn;
  cần thử trên VPS bằng `aigt assets rules status COIN`).
- Hành vi chính xác khi `--no-demo-portfolio` được dùng trên VPS có journal ở đường khác.
