# Checklist: đưa runtime slot 1/3 túi, 5 coin lên Binance Demo (ADR-007)

Trạng thái 2026-10-15: **G2/G3 đã triển khai, G4 đang soak; G5/G6 chưa thực hiện.** Chuẩn bị G5 đã diễn tập local;
key và soak đạt còn chờ. Julius đã chọn Jev chỉ tư vấn cho Demo slot (phụ lục ADR-007 ngày 15/10); G5 và G6 vẫn cần duyệt riêng.
Cơ sở: [ADR-007](../decisions/007-slot-rotation-five-coin-runtime.md), [ADR-006](../decisions/006-three-coin-runtime.md),
[checklist 3 coin](three-coin-demo-rollout-checklist.md) (chỉ có trên nhánh `feature/three-coin-runtime`),
[Demo runbook](../binance-demo-execution-runbook.md). Lệnh dưới đây đã đối chiếu với CLI của nhánh `feature/jev-advisory` (kế thừa `prep/g5-demo`).

## 0. Cấu hình sẽ chạy

| Mục | Giá trị |
|---|---|
| Profile quy tắc | `setup2_slots_v1`: Donchian **25/10** H4, EMA200/50, ADX14 > 20 và đang tăng, volume ×1,2, volume profile, ATR14×3, **stop chặn 5%** |
| Coin | ETH, NEAR, SOL, ZEC và HYPE, tất cả `both`. HYPE Spot chạy trên **proxy giá Perp** trước 2026-09-24 (ngưỡng làm nóng 400 nến, miễn trừ gate ≥ 6 lệnh), xem phụ lục ADR-007 |
| Túi | Spot 50% / Perp short 1x 50%, tách riêng; vị thế tối đa **1/3 vốn thực hiện của túi** nhân hệ số ATR |
| Danh mục | Tối đa **3 vị thế** trên hai túi, một vị thế mỗi coin; cổng PnL sổ đang mở; xoay vị thế khi ADX hơn > 5 điểm |
| Jev | `jev_mode=advisory`: luật và cổng rủi ro quyết định; Jev chỉ tư vấn. Thiếu câu trả lời thì chờ tối đa 120 giây từ đóng nến; xoay chỉ sau khi luật và rủi ro cho phép |
| Rủi ro | Dừng lỗ ngày 3%, DD 15%, Perp 1x isolated |
| Vốn Demo | 1.000 USDT (mặc định nghiên cứu) |

## 1. Hệ quả cần chấp nhận trước

- [ ] **5 coin và HYPE chưa được đo.** Stage 0 (`rotation-five`, BTC thay HYPE) kiểm tra cơ chế 5 coin trên 3 vị thế; kết quả phải được ghi vào
      `docs/rotating-positions-research.md` **trước G3**, dù là "qua" hay không.
- [ ] Demo dùng tín hiệu luật như replay; Jev chỉ tư vấn, chưa được đo là có ích. Khớp lệnh thật có trượt giá, xoay vị thế đóng rồi mở và chờ tư vấn tối đa 120 giây có thể làm kết quả khác replay.
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
| G5 | Cấp key, chọn venue, xác minh leverage, `execution demo configure` và `activate` 9 route; HYPE Spot chờ đợt sau |
| G6 | Khởi động container `demo-execution` |

## Giai đoạn 1: chuẩn bị, chưa chạm VPS

- [ ] `uv run pytest -q` trên đúng commit; chấp nhận duy nhất lỗi có sẵn `test_perp_collection_migration` (phụ thuộc ngày).
- [ ] Ghi hash commit sẽ deploy: `________`. Bộ tham chiếu nghiên cứu cho `result_id` không đổi (ghi ở CHANGELOG).
- [ ] Credit OpenRouter nạp đủ; `aigt provider list` ổn.
- [ ] Demo key (quyền Spot và USD-M Futures, không rút): `uv run aigt connect bnb demo` (nhập ẩn). Không dán key vào chat hay `.env`.
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
- [x] Julius duyệt push (G1) và deploy (G2). Chỉ rebuild `worker`, `web` (và `admin` khi cần). Không đổi schema (vẫn v23).
      **Đã deploy 2026-10-13 13:53Z, commit `846e744`** (gồm bản sửa nóng Hyperliquid đang chạy). Bài học: khởi động `web` trước rồi mới tới `worker`,
      hoặc khởi động lại riêng `worker` sau khi web healthy; khởi động đồng thời làm ba luồng nền của worker chết vì tranh khoá database.
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
- [ ] `aigt assets rules evaluate CANDIDATE_ID [--funding-id FUNDING_ID_MOI]`, rồi
      `aigt assets rules activate CANDIDATE_ID --evaluation-id SOAK_EVALUATION_ID`. Perp phải có funding phủ tới cuối cửa sổ mới;
      dùng đúng report root của replay ban đầu. Các lệnh và ID hiện tại nằm ở giai đoạn 5.0.
- [ ] **HYPE Spot (quyết định 2026-10-14):** snapshot Spot 4h cần ≥ 35 nến 1d trên Binance Spot, HYPE Spot đủ vào khoảng 2026-10-29,
      trước đó route báo `error:StaleMarketData` và không ghi heartbeat. Sau mốc đó, khi worker log `HYPEUSDT:spot_4h` không còn lỗi:
      `aigt setup2 candidate --symbol HYPE --market spot --profile setup2_slots_v1 --actor julius --reason "restart soak after 1d history" --retire-open`
      (đóng cả campaign cũ), `aigt setup2 backfill --symbol HYPE --market spot`, `aigt assets rules replay NEW_ID --engine v2`,
      rồi `start-soak` lại. Soak HYPE Spot sớm nhất xong khoảng 2026-11-12; 9 route kia không phụ thuộc.

## Giai đoạn 5: chuẩn bị, cấu hình và kích hoạt Demo (G5)

**Chưa thực hiện trên VPS.** Các lệnh trong mục này dành cho Julius sau khi duyệt G5. Đợt đầu chỉ kích hoạt
ETH/NEAR/SOL/ZEC Spot và Perp, HYPE Perp (**9 route**). Giữ nguyên active set `both` của 5 coin và mọi candidate/hash;
không `active-set switch` để bỏ HYPE Spot. HYPE Spot chờ soak mới sau 2026-10-29, sớm nhất khoảng 2026-11-12.

### 5.0. Hoàn tất bằng chứng G4, trước preflight G5

- [ ] Sau **2026-10-28 04:11Z (11:11 giờ Việt Nam)** mới đánh giá cả 9 route. Campaign muộn nhất trong đợt này
      bắt đầu `2026-10-14T04:10:05.919934Z` (HYPE Perp), nên 04:10:00Z vẫn còn sớm.
- [ ] Kiểm tra lại `assets rules status COIN` và bảng dưới trước khi dùng: đây là ID đọc trên VPS ngày 15/10,
      không phải bằng chứng soak đã qua. Không dùng ID replay hoặc ID của HYPE Spot để activate route khác.

| Route | Candidate ID luồng soak hiện tại | Hash rule (12 ký tự đầu) |
|---|---|---|
| ETH Perp | `ethusdt-perp-intraday-setup2-slots-v1-1791947819` | `b706c00f5045` |
| ETH Spot | `ethusdt-spot-4h-setup2-slots-v1-1791947815` | `1ca020df8cd0` |
| HYPE Perp | `hypeusdt-perp-intraday-setup2-slots-v1-1791947840` | `7e7d8b193e18` |
| NEAR Perp | `nearusdt-perp-intraday-setup2-slots-v1-1791947823` | `76bf45543b5f` |
| NEAR Spot | `nearusdt-spot-4h-setup2-slots-v1-1791947186` | `df6f3fb08332` |
| SOL Perp | `solusdt-perp-intraday-setup2-slots-v1-1791947827` | `3799cfd48b2e` |
| SOL Spot | `solusdt-spot-4h-setup2-slots-v1-1791947194` | `aead22dcf28a` |
| ZEC Perp | `zecusdt-perp-intraday-setup2-slots-v1-1791947831` | `d7c8ba873ac5` |
| ZEC Spot | `zecusdt-spot-4h-setup2-slots-v1-1791947202` | `5822af486aa6` |

Với **từng** candidate ở bảng: cập nhật dữ liệu công khai nếu thiếu nến, đánh giá soak, đọc kết quả, rồi promote rule
bằng đúng `evaluation_id` có `kind=soak`, `status=pass`. `deferred`/`reject` thì dừng route đó; không tạo evaluation thay thế.
Đặc biệt ZEC/HYPE Perp vẫn có thể bị loại khi replay mới chạm sàn −5%.

Các hàm dưới chạy trong **Bash trên host VPS**, từ một phiên SSH có TTY. Chúng chỉ được định nghĩa trong shell hiện tại:

```bash
cd /home/julius/AIgorithmic-trading
dc() {
  docker compose --env-file /home/julius/AIgorithmic-trading/.env.intraday \
    -f /home/julius/AIgorithmic-trading/deploy/intraday/compose.yaml "$@"
}
admin() { dc --profile admin run --rm --no-deps admin "$@"; }
demo() { dc --profile demo run --rm --no-deps demo-execution "$@"; }
demo_source=/app/state/intraday/intraday.sqlite3
demo_journal=/app/state/execution/binance-demo.sqlite3
demo_key=/run/execution-secrets/binance-demo.json
replay_root=/app/state/replay-reports
execution_demo() {
  local action="$1"
  shift
  demo execution demo "$action" --execution-database "$demo_journal" "$@"
}
```

Ví dụ ETH Spot; lặp tương tự cho 8 dòng còn lại, dùng đúng ID trong bảng:

```bash
coin=ETH
market=spot
candidate_id=ethusdt-spot-4h-setup2-slots-v1-1791947815
admin assets rules status "$coin" --database "$demo_source"
# Chỉ backfill nếu thiếu/cũ; không tạo candidate mới hay khởi động soak lại.
admin setup2 backfill --symbol "$coin" --market "$market" --database "$demo_source"
admin assets rules evaluate "$candidate_id" --database "$demo_source" --report-dir "$replay_root"
# Đọc JSON vừa in: chỉ đi tiếp nếu status=pass và kind=soak.
read -r -p 'evaluation_id của soak vừa PASS: ' soak_id
admin assets rules activate "$candidate_id" --evaluation-id "$soak_id" \
  --database "$demo_source" --report-dir "$replay_root"
```

**Perp cần thêm funding mới khi evaluate**; không tái dùng snapshot chỉ phủ đến 14/10. Sau backfill Perp, thay dòng evaluate ở trên bằng:

```bash
# Đặt coin, market=perp và candidate_id theo đúng dòng trong bảng.
funding_start=2024-04-10T00:00:00+00:00
if [ "$coin" = HYPE ]; then funding_start=2025-08-06T16:00:00+00:00; fi
funding_end=$(date -u -d "$(date -u +%F) $((10#$(date -u +%H) / 4 * 4)):00:00 UTC" +%Y-%m-%dT%H:%M:%S+00:00)
admin replay funding fetch "$coin" --from "$funding_start" --to "$funding_end" \
  --database "$demo_source" --report-dir "$replay_root"
read -r -p 'funding_id vừa tạo cho đúng coin: ' funding_id
admin assets rules evaluate "$candidate_id" --funding-id "$funding_id" \
  --database "$demo_source" --report-dir "$replay_root"
# Sau khi PASS: lấy soak_id rồi assets rules activate như ví dụ Spot.
```

`evaluate` ghi evaluation/report mới và có thể đóng campaign khi reject; đây là bước G4 có chủ ý của Julius vào ngày duyệt.
Funding fetch chỉ đọc API công khai, ghi snapshot ở report volume. Không đổi rule hay lùi ngày campaign.

### 5.1. Việc Julius tự làm: key và tài khoản

- [ ] Tạo **HMAC key Binance Demo** với quyền đọc và giao dịch **Spot + USD-M Futures**, không rút; tài khoản chuyên dụng,
      không giao dịch tay, không reset số dư. Không đưa key/secret vào chat, đối số lệnh hay `.env`.
- [ ] Trên giao diện Binance Demo chọn **One-way**, **Single-asset**, **Isolated cho từng cặp Perp** trong 5 coin.
      Wizard không sửa các chế độ này. Chuẩn bị ≥500 USDT khả dụng ở mỗi tài khoản Spot và Futures.
- [ ] Trên máy local có checkout và terminal tương tác, chạy:

```bash
cd /home/julius/julius-workspace/ai-labs/projects/system-trading
uv run aigt connect bnb demo
```

Wizard nhập ẩn, chỉ gọi API GET; lưu `state/execution-secrets/binance-demo.json` (0600) trong thư mục 0700 và tạo lock riêng.
Không cần source DB/champion/soak. Kết nối thành công chỉ xác nhận quyền đọc; **chưa xác nhận quyền đặt lệnh**.
Cảnh báo BTC 3x là kiểm tra legacy, không thay yêu cầu 1x của năm cặp trong ADR-007. Nếu key đã tồn tại, Keep là mặc định;
Replace/Clear có kiểm tra journal và trạng thái tài khoản riêng, không dùng để sửa một campaign đang chạy.

**Đưa file lên VPS (Julius thực hiện, lần cấp key đầu tiên):** kiểm tra đích chưa có key; nếu đã có thì dừng để đối chiếu
journal/rotation guard, không ghi đè. Ngày 15/10 cả hai thư mục mặc định dưới đây **chưa tồn tại**.

```bash
ssh julius@192.168.21.57 'install -d -m 700 /home/julius/AIgorithmic-trading/state/execution /home/julius/AIgorithmic-trading/state/execution-secrets'
ssh julius@192.168.21.57 'test ! -e /home/julius/AIgorithmic-trading/state/execution-secrets/binance-demo.json'
# Chỉ chạy scp sau khi test trên thành công; -p giữ mode 0600 của file local.
scp -p state/execution-secrets/binance-demo.json \
  julius@192.168.21.57:/home/julius/AIgorithmic-trading/state/execution-secrets/binance-demo.json
ssh julius@192.168.21.57 'chmod 700 /home/julius/AIgorithmic-trading/state/execution /home/julius/AIgorithmic-trading/state/execution-secrets; chmod 600 /home/julius/AIgorithmic-trading/state/execution-secrets/binance-demo.json; stat -c "%n %a %u:%g" /home/julius/AIgorithmic-trading/state/execution /home/julius/AIgorithmic-trading/state/execution-secrets /home/julius/AIgorithmic-trading/state/execution-secrets/binance-demo.json'
```

Mặc định service chạy UID:GID **1000:1000**, trùng user Julius trên VPS. File key phải thuộc UID thực thi (loader kiểm UID và mode 0600),
execution dir phải ghi được, secrets dir chỉ đọc trong container. Nếu dùng `AIGT_EXECUTION_UID`/`AIGT_EXECUTION_GID` khác,
Julius sửa ownership **từng đường dẫn chuyên dụng** cho khớp; không chown volume soak hay cấp quyền public.
Bind mặc định tính từ `deploy/intraday/compose.yaml`: `../../state/execution` → `/app/state/execution`,
`../../state/execution-secrets` → `/run/execution-secrets` (read-only). Có thể chỉ định đường dẫn host tuyệt đối qua
`AIGT_EXECUTION_HOST_DIR`/`AIGT_EXECUTION_SECRETS_HOST_DIR`; không đặt nội dung key vào các biến này.
`create_host_path: false` khiến thiếu thư mục là lỗi khởi động, không tự tạo.

### 5.2. Chọn venue trước, đặt leverage, rồi configure paused

Sau khi key/directories sẵn sàng và Julius duyệt G5, trên host VPS dùng các hàm ở 5.0:

```bash
# Build riêng image Demo trên commit đã được duyệt; chưa khởi động runner.
dc --profile demo build demo-execution
# Read-only check dưới đúng UID/container path, không đọc nội dung key.
dc --profile demo run --rm --no-deps --entrypoint /app/.venv/bin/python demo-execution -c \
  'import os,sqlite3; from pathlib import Path; p=Path("/app/state/intraday/intraday.sqlite3"); c=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True); print("source_readable",c.execute("PRAGMA user_version").fetchone()); c.close(); print("journal_writable",os.access("/app/state/execution",os.W_OK)); s=Path("/run/execution-secrets/binance-demo.json").stat(); print("key_mode_uid_gid",oct(s.st_mode&0o777),s.st_uid,s.st_gid,"runner_uid",os.geteuid())'

# Configure lấy nguyên active set: phải có venue selection của cả 10 route, kể cả HYPE Spot đang chờ.
# Chọn venue chỉ ghi metadata trong source DB, không activate giao dịch hay thay candidate hash.
for coin in ETH NEAR SOL ZEC HYPE; do
  for market in spot perp; do
    admin assets scan "$coin" --market "$market" --notional 1000 --database "$demo_source" || break 2
    admin assets venue set "$coin" --market "$market" --venue bnb --environment demo \
      --database "$demo_source" || break 2
  done
done

# Cần TTY, không dùng -T. Mỗi lần nhập 1, rồi chọn Confirm; Cancel là mặc định.
for coin in ETH NEAR SOL ZEC HYPE; do
  demo perp "$coin" -leverage --source-database "$demo_source" \
    --execution-database "$demo_journal" --secrets-file "$demo_key" || break
  demo perp "$coin" --source-database "$demo_source" \
    --execution-database "$demo_journal" --secrets-file "$demo_key" || break
done
# Kiểm cả leverage sàn và cấu hình đều 1x, Isolated, không pending/unknown request, paused hoặc not_activated.
execution_demo configure --from-active-set --jev-mode advisory --capital 1000 \
  --source-database "$demo_source" --secrets-file "$demo_key"
execution_demo status --multi
```

- [ ] Dừng khi scan/venue/leverage lỗi; không chạy bước sau chỉ vì shell còn mở. `venue set` tự scan lại nếu không truyền
      `--scan-id`; ID scan chỉ dùng lại trong 60 giây. Scan hiện **không in min-notional/step**, cần thêm `exchangeInfo` như bảng diễn tập dưới.
- [ ] Leverage ghi request vào **cùng journal sẽ chạy Demo**, POST settings khi cần đổi sau Confirm, rồi GET xác minh; không đặt lệnh.
      Dù sàn đã là 1x, vẫn Confirm 1 để lưu verified setting vào journal (mặc định journal mới là 3x).
- [ ] `configure` gọi GET kiểm cả 10 route và ghi journal, không đòi champion hay soak. Kết quả:
      `profile=setup2_slots_v1`, `jev_mode=advisory`, `paused=true`, `enabled=false`, 10 route trong allocation, chưa route nào được activate;
      Spot stop 5%, gross 100%, Perp 50%, margin 50%, dừng lỗ ngày 3%, DD 15%.
- [ ] HYPE Spot không cần champion/soak để configure. Nó **không tự active**; chỉ activate 9 route dưới đây.
      Runtime vẫn đọc quote/account của các route trong allocation để quản lý rủi ro; lỗi API chung có thể pause portfolio.
- [ ] Mọi configure/preflight/activate/run dùng cùng `/app/state/intraday/intraday.sqlite3` và journal trên. Không configure
      từ đường source trên host rồi chuyển sang đường container: runtime so khớp chuỗi đường dẫn campaign.
      `admin` chỉ dùng để ghi source/reports; không có mount key/journal Demo. Không chạy execution trong worker đang soak.

### 5.3. Preflight và activate từng route

Lặp cho 9 dòng trong bảng 5.0, lấy đúng `soak_id` đã promote cho dòng đó:

```bash
# Các biến coin, market, soak_id phải thuộc cùng một dòng đã hoàn tất 5.0.
execution_demo preflight --multi --symbol "$coin" --market "$market" \
  --source-database "$demo_source" --secrets-file "$demo_key"
# Chỉ tiếp tục sau khi xem account/quote/filter và target_notional_cap.
execution_demo activate --multi --symbol "$coin" --market "$market" --evaluation-id "$soak_id" \
  --source-database "$demo_source" --secrets-file "$demo_key"
execution_demo status --multi
```

- [ ] Preflight báo `target_notional_cap` = **166,666… USDT**, chưa nhân ATR. Preflight kiểm champion và API GET,
      **không xác nhận soak, profile hoặc minimum cho một tín hiệu cụ thể thay activate/runtime**.
- [ ] Activate kiểm champion cùng profile, đúng latest passing soak của campaign đã `promoted`, hash/replay binding,
      trạng thái tài khoản/rủi ro và leverage 1x; ghi route active và bỏ pause toàn portfolio. Không gửi lệnh.
      Vì vậy **chưa bật runner** khi đang activate các route; nếu runner đã tồn tại phải dừng riêng nó trước khi cấu hình batch.
- [ ] Cuối G5: đúng 9 route active; **không có `spot:HYPEUSDT` active**. Không có fake soak trên DB bản sao hoặc VPS.
- [ ] Key, quyền đặt lệnh, read-back leverage và preflight có tài khoản thật: **chờ key**. Soak: **chờ 28/10**, chưa bảo đảm pass.
- [ ] Quyết định Jev: **đã chọn chế độ tư vấn** (`--jev-mode advisory`) theo [phụ lục ADR-007](../decisions/007-slot-rotation-five-coin-runtime.md#jev-chỉ-tư-vấn-2026-10-15).
      Kiểm allocation đúng chế độ; luật và cổng rủi ro quyết định, Jev không phủ quyết. Đổi chế độ cần paused và flat.

## Giai đoạn 6: chạy có giám sát và đối chiếu (G6 riêng)

**Các lệnh này có thể đặt lệnh Demo. Không chạy trong đợt chuẩn bị 15/10.** Chỉ sau G5 và Julius duyệt G6:

Ghi nhận worker trước khi thử, và kiểm không có runner Demo nào khác:

```bash
worker_before=$(dc ps -q worker)
dc --profile demo ps -a demo-execution
```

Nếu Julius chọn thử một chu kỳ, chạy riêng khối này rồi đọc kết quả trước khi bật service:

```bash
execution_demo run --multi --once --source-database "$demo_source" --secrets-file "$demo_key"
execution_demo status --multi
```

Chỉ sau khi kết quả đã được kiểm tra và Julius cho chạy liên tục (không còn one-off runner đang chạy):

```bash
dc --profile demo up -d --no-deps demo-execution
test "$worker_before" = "$(dc ps -q worker)"
dc --profile demo logs --tail 100 demo-execution
execution_demo status --multi
```

`--once` là **một chu kỳ có thể submit/cancel/protect/close**, không phải dry-run hay một giao dịch khứ hồi chắc chắn.
Service mặc định interval 30 giây, `restart: "no"`, source/key read-only, journal riêng ghi được, không có dependency lên worker.
Nếu chu kỳ báo paused/error/unknown, giữ giám sát và đối soát trước khi chạy tiếp. Không restart soak để sửa Demo.

- [ ] `slot_selection` trong portfolio journal: số vị thế, PnL sổ, từng `free`/`rotate`/`blocked` và lý do.
      Dùng `execution demo status`, không dùng `aigt positions` (vị thế paper).
- [ ] Đối chiếu `setup2_observations` với replay cùng cửa sổ: ADX, ATR, cờ vào/thoát; giữ khác biệt Jev/fill trong báo cáo.
- [ ] Acceptance có giám sát: entry đủ điều kiện → stop gốc được sàn xác nhận → close → fills/phí/số dư khớp journal.
      Thiếu/lỗi Jev chỉ chờ tối đa 120 giây từ đóng nến; sau đó luật vẫn được vào nếu rủi ro/giá cho phép. Ghi rõ điểm chặn của luật, rủi ro hoặc giá; không tạo tín hiệu giả để nghiệm thu.
- [ ] Stop là bên gần hơn giữa ATR×3 và 5%, chỉ siết; thoát `donchian_exit`/`rotation_exit`. `portfolio_loss_limit`,
      cash drift, `protection_failed` hoặc `active_set_changed_reconfigure` cần dừng entry và xử lý có giám sát.
- [ ] Compose truyền riêng `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; không truyền toàn bộ env file vào Demo.
      Không in `docker compose config`/container environment để kiểm token. `notify_demo` lần đầu ghi `BASELINE` và sự kiện
      đã có rồi return, không gửi lịch sử. Baseline được ghi **sau chu kỳ đầu**, nên lệnh có ngay trong chu kỳ ấy cũng không gửi lại;
      dùng journal/status để kiểm lệnh đầu. Những sự kiện tiếp theo được gửi một lần, thử lại tối đa 5 lần khi lỗi.

### Pause, flatten và dừng riêng runner

```bash
execution_demo pause --multi
execution_demo status --multi
# Vẫn cần key; có thể submit lệnh đóng thật và cancel stop. Không chạy trong đợt chuẩn bị.
execution_demo flatten --multi --secrets-file "$demo_key"
execution_demo status --multi
# Chỉ sau khi đã xác nhận flat và không pending/unknown; giữ journal và key.
dc --profile demo stop demo-execution
```

Pause không cần source/key, không gọi API, giữ stop gốc và chỉ chặn entry; runner vẫn có thể reconcile/thoát để quản lý rủi ro.
Nếu journal có nhiều tài khoản, thêm `--account-id` từ status. Flatten không cần source, nhưng cần key/quyền giao dịch;
không coi ACK là đã flat. Khi thao tác gặp journal lock, đợi chu kỳ xong rồi thử lại, không chạy thêm runner thứ hai.

## Lùi lại

Dùng `pause --multi`, `flatten --multi` và kiểm flat/reconciled với đầy đủ đường journal/key như giai đoạn 6.
Sau đó mới `active-set switch` về version trước nếu Julius duyệt riêng, khi paused và flat. Giữ nguyên journal và dữ liệu soak.

## Chưa kiểm chứng

- Acceptance có giám sát với tiền Demo cho đường lệnh, stop và đối soát (chưa từng có), cũng như ghi và đọc lại leverage.
- Hành vi thật của Demo với HYPE, ZEC (thanh khoản, bước giá, lệnh tối thiểu).
- Jev chỉ được ghi để đối chiếu, chưa được đo là có ích; xoay vị thế chạy tuần tự (đóng rồi mở) nên có trượt giá giữa hai lệnh.

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

## Chạy thử G5 trên database tạm (2026-10-15)

**Phạm vi đã làm:** worktree `../system-trading-g5-prep`, nhánh `prep/g5-demo`, xuất phát `18f15da`.
VPS chỉ `stat`/`ls`, Compose `ps` và SELECT qua SQLite `mode=ro` trong worker; `scp` chỉ lấy backup về local.
Không đọc/in `.env`, key, provider/execution secrets; không gọi API giao dịch, chạy runner hay thay trạng thái trên VPS.

### Bằng chứng và giới hạn diễn tập

- Bản backup và manifest nguyên gốc hiện có thêm bản sao ngoài VPS ở
  `/home/julius/.local/state/aigorithmic-trading/g5-rehearsal/`:
  `intraday-20261013T134644506616Z.sqlite3` và **`intraday-20261013T134644506616Z.manifest.json`**
  (manifest không có `.sqlite3` trong tên). 2.169.839.616 byte, SHA-256
  `6d25bf9225a33202ec315c26f4e5f27bc4e4030a57b1bd90f2160356caefeaa5`, `backup verify`: `status=ok`, integrity `ok`.
- Bản diễn tập là **bản sao thứ hai**, `g5-rehearsal/rehearsal-20261015/source.sqlite3`; không sửa backup gốc.
  Tạo active set bằng đúng cờ G3, chọn venue và tạo 10 candidate bằng cờ G4 trên bản sao. Payload active set giữ
  profile/neo/proxy/waiver như VPS. Không cần backfill/replay/campaign mới để kiểm configure; bản sao không có champion G4,
  không chứa dữ liệu soak 14 ngày và **không được dùng để promote**. Không chép DB live hay tạo fake soak evaluation. Kiểm cuối: SHA backup không đổi;
  số evaluation gốc và bản sao giống nhau (replay gate 8, scoped 23, portfolio soak 3).
- Kiểm VPS cuối đợt: worker ID vẫn `97c32bee47f5…`, active set vẫn `c3d57eda19cd…`. Source directory 0755,
  DB/WAL/SHM đều 0644, owner 0:0; kiểm đọc bằng UID thực thi trong one-off Demo vẫn cần làm ở 5.2 khi có bind directories.
- Output CLI, scan, cấu hình offline và script tái lập nằm trong thư mục diễn tập trên; `evidence/` lưu help, raw filters/book,
  bảng tính lượng lệnh, kết quả pytest và snapshot ID/hash campaign đọc trên VPS.
- Chạy CLI configure/preflight/activate với `--secrets-file` trỏ đường **chưa tồn tại**: cả ba dừng đúng ở
  `Demo credentials require a regular 0600 file`; chưa tạo portfolio hay gọi Demo private API (**chờ key**).
- Để kiểm sâu hơn mà không có key, chạy đúng dispatcher CLI trên bản source tạm, thay **credential loader và adapter tại biên**
  bằng fixture; không tạo file key và chặn mọi transport mạng. Journal riêng `offline-execution.sqlite3` ghi cấu hình
  `paused=true`, `enabled=false`, allocation 10 route, 0 route active, 0 order. `_envelope` = `1.00/0.50/0.50`,
  daily/DD floors `0.97/0.85`, Spot stop `0.05`, cap `166.666666…`, tại multiplier `0.40` là `66.666666…` USDT.
  Đây là kiểm logic/journal, **không phải acceptance tài khoản Binance Demo**; fixture tài khoản không chứng minh leverage thật.
- Khi giả lập biên như trên nhưng giữ **source thật của bản sao**, preflight/activate của 10 route dừng ở
  `no active champion for this symbol and scope`. Không sửa registry hay chèn pass để vượt cổng.
- Hai test `test_nine_routes_run_while_hype_spot_waits_for_its_own_evidence[champion|soak]` dùng fixture hoàn toàn riêng:
  cấu hình 10 route, activate 9 với bằng chứng fixture, HYPE Spot bị từ chối khi thiếu champion hoặc passing soak;
  portfolio không đổi sau lần từ chối và chu kỳ của 9 route vẫn `running`. Chỉ test fixture dùng soak giả,
  không ghi fixture vào backup, bản source diễn tập hay VPS. Test lifecycle v2 có sẵn kiểm replay → soak → promote và từ chối nhầm ID.

Các lệnh local đã chạy (đường dẫn đầy đủ nằm trong output/script bên ngoài repo):

```bash
# Trong worktree riêng; dùng venv có sẵn với uv --frozen --no-sync và PYTHONPATH trỏ worktree.
uv run aigt backup verify /home/julius/.local/state/aigorithmic-trading/g5-rehearsal/intraday-20261013T134644506616Z.sqlite3
# R là bản sao diễn tập, không phải database runtime.
R=/home/julius/.local/state/aigorithmic-trading/g5-rehearsal/rehearsal-20261015
aigt active-set init --database "$R/source.sqlite3" --profile setup2_slots_v1 \
  --anchor 2024-01-01 --anchor HYPE=2025-06-01 \
  --coin ETH:both --coin NEAR:both --coin SOL:both --coin ZEC:both --coin HYPE:both \
  --warmup HYPE=400 --spot-proxy HYPE=2026-09-24T08:00:00Z --waive-min-trades HYPE:spot \
  --waive-net-return ZEC:perp --waive-net-return HYPE:perp \
  --actor julius --reason 'ADR-007 rollout rehearsal; local only' \
  --execution-database "$R/execution.sqlite3" --no-demo-portfolio
# Lặp cả hai market của 5 coin trên bản sao; HYPE Spot chỉ để kiểm cấu hình đầy đủ.
aigt assets scan COIN --market MARKET --notional 1000 --database "$R/source.sqlite3"
aigt assets venue set COIN --market MARKET --venue bnb --environment demo \
  --scan-id SCAN_ID_VUA_TAO --database "$R/source.sqlite3"
aigt setup2 candidate --symbol COIN --market MARKET --profile setup2_slots_v1 \
  --actor julius --reason 'ADR-007 local G5 rehearsal' --retire-open --database "$R/source.sqlite3"
# Thiếu key: lệnh thật từ chối; cùng dispatcher được kiểm với boundary fixture như mô tả ở trên.
aigt execution demo configure --from-active-set --capital 1000 --source-database "$R/source.sqlite3" \
  --execution-database "$R/execution.sqlite3" --secrets-file "$R/not-provisioned/binance-demo.json"
```

### Đối chiếu lệnh với parser và code

Tất cả các lệnh dưới đã chạy `uv run --frozen --no-sync aigt … --help` thành công và đối chiếu code.
Vị trí chạy là vị trí **dự kiến khi triển khai**, không khẳng định các lệnh ghi đã chạy trên VPS.
`execution-database` là journal riêng, `database` là source đang soak; tránh lẫn hai cờ.

| Lệnh/cờ | Tương tác | Chạy ở đâu | Ghi gì | Gọi API Demo |
|---|---|---|---|---|
| `connect bnb demo [--secrets-file …] [--execution-database …]` | TTY, nhập ẩn; Keep/Replace/Clear nếu đã có | Local/host có checkout; bypass Docker dispatch | File JSON 0600, thư mục 0700 và lock; không activate | GET account hai thị trường; chưa chứng minh quyền order |
| `perp COIN -leverage` (hoặc `--leverage`) + ba đường source/journal/key | TTY, nhập 1–10, preview, Cancel/Confirm | One-off `demo-execution`, sau venue selection | Requests/events và verified leverage trong journal | GET; có thể POST leverage sau Confirm; không đặt order |
| `assets venue set COIN --market spot/perp --venue bnb --environment demo [--scan-id …] --database …` | Không | `admin` | Scan nếu thiếu ID, `asset_venue_routes`, catalog/request metadata trong source | Public read-only scan; không key |
| `assets scan COIN --market spot/perp --notional 1000 --database …` | Không | Local tạm; triển khai ở `admin` | `asset_market_scans` trong source | Public Demo/Testnet, gồm POST `/info` chỉ đọc ở Hyperliquid; không order |
| `execution demo configure --from-active-set --capital 1000` + source/journal/key | Không | One-off `demo-execution` | Journal: allocation, baseline, paused; không sửa source | GET cho **10** route; cần key |
| `execution demo preflight --multi --symbol COIN --market MARKET` + source/journal/key | Không | One-off `demo-execution` | Có thể tạo/migrate journal khi mở; không activate, source mode=ro | GET clock/account/filter/quote |
| `execution demo activate --multi --symbol COIN --market MARKET --evaluation-id SOAK_ID` + source/journal/key | Không; câu lệnh là phê duyệt | One-off `demo-execution`, G5 | Route/campaign/risk state ở journal; source mode=ro | GET, không submit order |
| `execution demo run --multi [--once]` + source/journal/key | Không | One-off **hoặc** service liên tục, G6 | Journal intents/fills/portfolio/notifications; source mode=ro | GET và có thể POST/DELETE order/stop |
| `execution demo pause --multi [--account-id …] --execution-database …` | Không | One-off `demo-execution` | Paused trong journal, giữ stop | Không; không cần source/key |
| `execution demo flatten --multi --execution-database … --secrets-file …` | Không | One-off `demo-execution` | Journal close/reconciliation/paused | GET + có thể order/stop writes; không cần source |
| `execution demo status [--multi] --execution-database …` | Không | One-off `demo-execution` | Có thể tạo/migrate journal; hiển thị last-sync | Không; không cần source/key |

`--source-database` **bắt buộc** với configure/preflight/activate/run. `configure` đã đặt multi ngầm; các lệnh còn lại phải truyền
`--multi` để tránh đường BTC legacy. Không lệnh execution nào tự nhảy vào admin qua deployment registry.

**Lỗi đã sửa:** `active-set init/switch --help` crash `TypeError: must be real number, not dict` do `-5% floor` bị argparse
coi là mẫu format. Đổi riêng chuỗi help thành `-5%% floor`; hai test tái hiện trước sửa rồi qua sau sửa.
Không đổi `intraday/scoped_gate.py`, `rule_allows_answers`, parameters hay content hash.

Suite đầy đủ phát hiện thêm lỗi đóng gói có sẵn: checkout sạch thiếu
`integrations/hermes/trading-ops/.env.template` vì `.gitignore` loại `.env.*`. Bổ sung đúng file mẫu công khai với URL loopback,
hai trường token trống, và ngoại lệ ignore riêng đường dẫn đó; 5 test gói Hermes qua. Không đọc file cấu hình/token runtime,
không cài hay bật Hermes. Test cũ được giữ nguyên, không bỏ kiểm tra để có kết quả xanh.

### Minimum và bước khối lượng: 9 route của đợt đầu

Đã chạy `assets scan … --notional 1000` cho cả 9 route, thêm HYPE Spot để cấu hình đủ active set: tất cả Binance Demo `listed`,
`selectable=true`. Scan ở 09:52–09:53Z ngày 15/10 có spread <10 bps, đủ depth cho 1.000 USDT trong snapshot;
HYPE Perp/ETH Spot/ZEC Spot có nhãn `depth_is_snapshot_lower_bound`. Scan không trả filter min/step, nên lấy thêm
`exchangeInfo` và depth **công khai trên Demo**, lưu raw trước khi tính (09:48Z). Không dùng filter hay giá mainnet thay Demo.

Cỡ so sánh `500/3 × 0,40 = 66,666… USDT`, lượng làm tròn **xuống**, dùng ask cho cả hai market theo `_place_slot_entry`.
Bảng là phép tính theo snapshot, không phải order đã được sàn chấp nhận:

| Route | Min USDT | Step coin | Tick USDT | Ask lúc đo | Lượng tại 0,40 | Notional sau tròn | Giảm cỡ |
|---|---:|---:|---:|---:|---:|---:|---:|
| ETH spot | 5 | 0.0001 | 0.01 | 2372.10000 | 0.0281 | 66.6560 | 0.016% |
| NEAR spot | 5 | 0.1 | 0.001 | 4.63100 | 14.3 | 66.2233 | 0.665% |
| SOL spot | 5 | 0.001 | 0.01 | 102.98000 | 0.647 | 66.6281 | 0.058% |
| ZEC spot | 5 | 0.001 | 0.01 | 1098.61000 | 0.060 | 65.9166 | 1.125% |
| ETH perp | 20 | 0.001 | 0.01 | 2371.06000 | 0.028 | 66.3897 | 0.415% |
| NEAR perp | 5 | 1 | 0.001 | 4.62500 | 14 | 64.7500 | 2.875% |
| SOL perp | 5 | 0.01 | 0.01 | 103.10000 | 0.64 | 65.9840 | 1.024% |
| ZEC perp | 5 | 0.001 | 0.01 | 1097.92000 | 0.060 | 65.8752 | 1.187% |
| HYPE perp | 5 | 0.01 | 0.001 | 79.78600 | 0.83 | 66.2224 | 0.666% |

- Tại multiplier **1 và 0,40**, cả 9 route qua min/step; Spot còn kiểm lượng stop sau reserve phí `×0,999`, giá stop 95%
  theo cùng book, vẫn >5 USDT. Chưa có route nào bị loại chỉ do minimum ở hai cỡ đã kiểm.
- **NEAR Perp đáng chú ý nhất**: step 1 NEAR, tại 0,40 giảm cỡ 2,875%; một bước lúc đo ≈4,625 USDT, có thể giảm gần 6,94%
  nếu mục tiêu nằm sát bước kế tiếp. ZEC Spot/Perp cũng giảm khoảng 1,1–1,2%; SOL Perp khoảng 1,0%. Không làm tròn lên để bù.
- `0,40` là tình huống cần kiểm, **không phải sàn multiplier trong code**. ATR tăng, vốn túi giảm hoặc free principal ít có thể
  làm cap nhỏ hơn nữa. ETH Perp có minimum **20 USDT**, sát sàn khi multiplier ≈0,12 trước làm tròn; route khác minimum 5,
  Spot còn phải đủ minimum cho stop sau phí/giảm giá. Runtime trả `rejected_quantity` khi lượng entry/stop không hợp lệ;
  không cố nới rule. Spread/gap, giá tính notional của sàn, số dư/phí và biến động sau snapshot vẫn có thể từ chối lệnh.
- Chạy lại scan + public exchangeInfo vào ngày G5; preflight có key sẽ in filter thật. Cách đọc filter đối chiếu
  [Spot filters](https://developers.binance.com/en/docs/products/spot/filters) và
  [USD-M exchange information](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
  Không suy từ `quantityPrecision` sang step và không dùng một minimum chung cho tất cả cặp.

### Jev và Telegram

Phần dưới giữ nguyên kết quả diễn tập trên `cad8779`; quyết định mới xem [phụ lục Jev chỉ tư vấn của ADR-007](../decisions/007-slot-rotation-five-coin-runtime.md#jev-chỉ-tư-vấn-2026-10-15).

Không sửa cổng Jev. `ScopedEvidenceSource.latest_decision` đòi primary signal tươi (Perp ≤45 giây, Spot ≤4 giờ) và provenance đúng.
Trong `_slots_cycle`, thiếu/stale/provenance sai câu trả lời trả `waiting_for_eligible_evidence`, có thể
`waiting_for_jev_answers` trong grace 600 giây. Khi có câu trả lời nhưng không được phép, route trả
`status=not_authorized`, kèm `reasons` từ `ScopedEntryGate`: Perp sai hướng là `setup2_requires_jev_sell` (return sớm),
còn đúng hướng nhưng yếu có `low_confidence`, `low_entry_quality`, `toxic_flow`, `regime_not_allowed`, `model_risk_high`.
Spot có `spot_long_only`, `low_confidence`, `regime_not_allowed`, `model_risk_high`, `toxic_flow`.
`rule_allows_answers` chỉ trả bool khi ghi soak/alert, không cấp phép vào lệnh Demo. Các điều kiện này độc lập với việc soak pass.

Compose đã khai báo đúng 3 biến Telegram tại service Demo. Test `test_demo_alerts_send_entry_with_stop_then_trail_moves_and_exit_once`
chứng minh lần đầu chỉ ghi BASELINE, bỏ lịch sử, sau đó entry/stop/exit gửi đúng một lần. Test retry giới hạn 5 lần và test env
cũng qua; không gửi Telegram thật trong đợt chuẩn bị. Cấp key/leverage/account-mode, acceptance order/protection/close,
soak 28/10 và quyết định Jev vẫn là những việc Julius cần hoàn tất.

### Kiểm thử

- Toàn bộ `uv run --frozen --no-sync pytest -q` trên worktree: **1.748 passed, 1 failed, 23 warnings**, 414,66 giây.
  Lỗi duy nhất được giữ đúng phạm vi cho phép:
  `tests/test_perp_collection_migration.py::test_cli_inheritance_and_readiness_show_collection_without_changing_champion`
  (`gate_version` trả `v1`, test đòi `gate-v2.1`). Không skip/xfail hay hạ tiêu chí test.
- Các lượt tập trung: 22 test active-set/proxy/waiver; 31 test diễn tập 9 route, lifecycle Spot/Perp, Telegram, leverage;
  5 test Hermes đều qua. Kiểm cú pháp 13 khối Bash bằng `bash -n`, không thực thi lệnh triển khai.
- Lượt suite đầu trong sandbox dừng ở collection vì không được ghi fixture của worktree nằm ngoài writable root;
  lượt có quyền filesystem chạy đủ phát hiện thiếu template Hermes (đã sửa), rồi lượt cuối chỉ còn lỗi migration trên.
- Không có acceptance có key hay kiểm chứng giao dịch Demo. Các bước cần key và soak thật được đánh dấu chờ ở giai đoạn 5/6.
