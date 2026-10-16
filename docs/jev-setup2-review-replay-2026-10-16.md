# Jev Setup-2 review — đợt 1 offline, 2026-10-16

Đợt 2 theo phê duyệt tiếp theo của Julius: [collect thật v2 và đánh giá tuning/test](jev-setup2-review-evaluation-2026-10-16.md).
Nội dung dưới đây ghi trạng thái và bằng chứng tại thời điểm kết thúc đợt 1.

Trạng thái: **hoàn tất đợt 1, dừng chờ duyệt gọi Jev thật; chưa đánh giá giá trị dự báo của A**.
Yêu cầu: `/home/julius/julius-workspace/tasks/inbox/note-2026-10-16-1102.md`.
Worktree `system-trading-jev-review`, nhánh `feature/jev-setup2-review`, base
`9eeb207442cef7aada3ad3fe0aaccf2d47bc5f14`.

Julius đã duyệt ngoại lệ đúng ba file replay trong
[phạm vi được duyệt](jev-review-protected-scope-2026-10-16.md). Phê duyệt không
bao gồm `--execute`. Không merge vào `feature/slot-runtime` hoặc deploy trước khi
đánh giá soak 28/10 xong; task này không cấp quyền merge/deploy sau mốc đó.

## Kết quả dry-run

Cửa sổ kết thúc tại **2026-10-14 00:00 UTC**. Với ETH/NEAR/SOL/ZEC, anchor là
2024-01-01, warmup 600 H4, bắt đầu replay 2024-04-10. HYPE anchor 2025-06-01,
warmup 400 H4; Spot có proxy giá Perp trước 2026-09-24 08:00 UTC, volume bằng 0,
không proxy volume profile. Mọi route dùng đúng candidate/thiết lập đã sao chép từ G5.

| Route | Nến `entry=True` cần review | Lệnh đóng của luật đơn độc |
|---|---:|---:|
| ETH Spot | 75 | 36 |
| ETH Perp | 98 | 46 |
| HYPE Spot | 0 | 0 |
| HYPE Perp | 38 | 20 |
| NEAR Spot | 113 | 40 |
| NEAR Perp | 90 | 39 |
| SOL Spot | 106 | 39 |
| SOL Perp | 103 | 42 |
| ZEC Spot | 114 | 47 |
| ZEC Perp | 59 | 31 |
| **Tổng** | **796** | **340** |

796 nến gồm cả tín hiệu khi đang có vị thế. Khử trùng theo model + phiên bản câu
hỏi + phiên bản encoder + state còn **379 state**, cache thật hiện có **0**;
dự kiến **379 request**, mỗi request hỏi cả ba câu. **Số lần gọi Jev thật: 0**.
Tuning có 538 nến, kiểm có 258 nến; đây là số nến tín hiệu, không phải số lệnh độc lập.

- Input token ước lượng: **313.532**, theo `ceil(số byte UTF-8 JSON chuẩn / 4)` mỗi request.
- Dự toán input bảo thủ: **1.641.652 token**, dùng số byte request + 1.024 mỗi request.
- Giá kiểm công khai ngày 16/10: **0,042 USD / triệu input token**, output miễn phí.
  Nguồn: [OpenRouter model](https://openrouter.ai/typesafe/jev-1.13),
  [API giá/provider](https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints).
- Chi phí ước lượng **0,013168344 USD**; dự toán bảo thủ **0,068949384 USD**;
  nếu mỗi state cần cả hai retry thì dự toán **0,206848152 USD**.

Tokenizer riêng của Jev không được cài local. Các số trên là ước lượng có công thức,
không phải `usage` đo được hoặc cam kết trần billing. Với 1 request/giây, một lượt
thành công cần ít nhất khoảng 6,3 phút, cộng độ trễ. Không dùng endpoint inference
hay token thật để ước lượng; chỉ đọc trang/API giá công khai.

Plan cuối: `/home/julius/.local/state/aigorithmic-trading/jev-review/collect-dry-run-20261016-envelope.json`.
Plan có hash của toàn bộ câu hỏi, cut points, state, danh tính config/dataset từng
route và tập cache đã dùng. `plan_hash` ràng buộc đầy đủ snapshot, tập khóa thiếu
và dự toán; `request_set_hash` ràng buộc tập request bất biến. `--execute` cần
`--approved-plan` trỏ tới receipt gốc cùng `--expected-plan-hash` đã duyệt, rồi
mới mở credential. Chạy tiếp dùng nguyên receipt gốc và chỉ được hỏi một tập con
của các khóa thiếu đã duyệt. Mất/thay cache làm phát sinh khóa mới bị từ chối.
Hash cuối ghi trong manifest kiểm chứng.

- `plan_hash`: **`a75d6cb312f42d62c52c8bfe5410ac23a990151a04ca947ae8ac7cbdbb9a0a3a`**.
- `request_set_hash`: `057501342b0be595e7c93d1a8e4ccc4e8c7f3d22400a254290e5eb9c1bd6e9bc`.
- SHA-256 file plan: `693e65c3b4ef6e05e10c7f9714d5be3fbcae2b6ac75fddabd4437ee17a9fed49`.
- 379 request body chuẩn hóa đã lưu ở `state/verification/jev-review-protected-20261016/requests-final.jsonl`,
  SHA-256 `92cd53094cbbbdf570a1cc10fef18f7181521ce9b956b1be6d130d26941320cb`.
- Hash tập câu trả lời hiện dùng, gồm các khóa còn thiếu:
  `d88096a81139ab4af007c65d16c5b3d9d56ce3e60d72d6c14738d0eadc2e4b52`.
  Đây là danh tính tập khóa thiếu, không phải bằng chứng có câu trả lời Jev thật.

## Bộ mã hóa và câu hỏi đã chốt

`STATE_VERSION=setup2-review-state-v1`,
`QUESTION_SET_VERSION=setup2-review-questions-v1`,
`LABEL_CONFIG_VERSION=setup2-review-labels-v1`.
Cut points và bảng mặc định được commit ở `c99b431`, trước khi đọc kết quả replay
baseline trong task này. Không chọn hoặc tối ưu cut point theo PnL.

14 nhóm token có thứ tự cố định: route, cửa sổ breakout, độ vượt kênh, ADX,
độ dốc ADX, khoảng cách EMA chậm, EMA nhanh có cùng hướng, volume, RSI H4,
độ dịch chuyển sáu nến, funding, ATR, độ dài nến kích hoạt và volume profile.
Output chỉ có chữ ASCII thường/dấu gạch dưới; số trong tên đặc trưng/cửa sổ
được viết bằng chữ, ví dụ `breakout_twenty_five`. Không có coin, timestamp,
giá hoặc số thập phân trong **state gửi Jev**. Plan audit vẫn giữ route/rule/nến
để ghép kết quả; các trường audit đó không đi vào request state.

| Đặc trưng | Cut points và đơn vị |
|---|---|
| Độ vượt kênh | 0,5 / 1,5 ATR |
| ADX | 25 / 40; khác ngưỡng rule ADX20, không đổi rule |
| Khoảng cách EMA200 | 1 / 3 ATR, trị tuyệt đối |
| Volume / MA20 | 1,2 / 2 |
| RSI14 H4 Wilder | 30 / 70 |
| Độ dịch chuyển sáu nến | 1 / 3 ATR, trị tuyệt đối |
| Funding đã settlement | −0,0001 / +0,0001 |
| ATR / close | 1% / 3% |
| High-low nến kích hoạt | 1 / 2 ATR |

Các dải dùng `< mốc thấp`, `<= mốc cao`, còn lại dải cao; độ dốc dùng so sánh
chính xác với nến trước. Dữ liệu thiếu/không hữu hạn có token `*_unknown`.
Funding Spot là `funding_not_applicable`. Encoder loại nến/settlement chưa có
ở thời điểm trigger đóng. Profile dùng đúng cửa sổ M15 kết thúc tại **trigger
open** của evaluator hiện có. Không dùng order book, OI, long/short hoặc `xv_`.

Ba câu hỏi: `action` (choice: enter_full/reduce/skip), `exhaustion` (noul),
`disorderly` (noul). Mỗi câu nói rõ giữ nhiều ngày, thoát kênh ngược 10 nến hoặc
stop gần hơn giữa 3×ATR và trần profile: 5% cho breakout 25, 10% cho breakout 30.
Tiêu chí chỉ dùng token đã cung cấp; RSI cực trị riêng lẻ không phải lý do veto.

Model request ghim **`typesafe/jev-1.13`**. Lưu model ID trả về; không tự đổi sang
1.14/latest. Các model ID thấy trong test/control là fixture, không phải bằng chứng
đã gọi model thật. Parser từ chối thiếu câu, sai type, bool/numeric string,
NaN, phân phối sai tổng hoặc choice không ứng với xác suất lớn nhất.

## Bảng A, cache và replay

Theo thứ tự: thiếu ý kiến → NO_VIEW; `P(skip)>=0,7` hoặc `P(exhaustion)>=0,7`
→ VETO; `P(reduce)+P(skip)>=0,6` hoặc `P(disorderly)>=0,6` → REDUCE; còn lại AGREE.
Hệ số lần lượt **NO_VIEW ×1, VETO ×0, REDUCE ×0,5, AGREE ×1**. Không tăng cỡ.
Đây là cấu hình mặc định để kiểm cơ chế, **chưa phải ngưỡng được hiệu chỉnh**.
Có seam ghép A/B nhưng branch B bị từ chối trong task này.

Cache thật mặc định: `~/.local/state/aigorithmic-trading/jev-review/answers.sqlite3`.
Dry-run/replay chỉ mở read-only; cache chưa tồn tại thì không tạo thư mục hoặc
database. Collector mới ghi cache. `answers` và `attempts` cấm UPDATE/DELETE;
câu trả lời hợp lệ được commit ngay để chạy tiếp sau gián đoạn. Timeout, HTTP
lỗi, response bị cắt hoặc sai định dạng không trở thành ý kiến dùng để giao dịch.
Retry tối đa hai lần, mặc định cách request ít nhất một giây; ba lỗi liên tiếp
mở circuit. Auth/credit/model sai dừng ngay. Không ghi `signals`/`model_calls`.

Lớp phủ mặc định `None`. Chỉ khi bật mới thêm payload lớp phủ vào fingerprint
và `inputs`; hash gắn tập câu trả lời thực sự dùng, cả khóa thiếu. Append state
không liên quan không đổi hash tập đã dùng. Lớp phủ materialize bất biến từ một
read snapshot; replay không hỏi Jev. Tắt lớp phủ không thêm bất cứ khóa output nào.

Đối chiếu chính là hai replay trên cùng dữ liệu, ghép `(rule_id, bar_close)`.
Nếu veto làm thay đổi đường vị thế, một tín hiệu bị bỏ mới có thể không tồn tại
trong các lệnh baseline. Khi đó bổ sung **một lệnh giả định bắt đầu flat tại
chính nến đó**, với vốn ban đầu của route, stop/exit/cost/funding/risk và đường
khớp dùng lại đúng engine. Causal features được tái sử dụng; dừng sau lệnh đầu.
`reference_trade_available=False` và `rule_only_pnl=None` vẫn giữ nguyên;
`counterfactual_pnl`, `isolated_trade`, phương pháp `isolated_flat_start` và ID
evidence riêng mô tả kết quả bổ sung. Không gọi ID này là `result_id` replay.
Lệnh giả định có thể chồng lấn và tương quan; **không cộng chúng thành PnL danh mục**.

## Dữ liệu và kiểm chứng

Database G5 nguồn chỉ đọc:
`~/.local/state/aigorithmic-trading/g5-rehearsal/rehearsal-20261015/source.sqlite3`.
Bản sao này có metadata/candidate nhưng thiếu extension nến Setup-2. Công cụ
chuẩn bị tạo database nghiên cứu riêng, giữ source nguyên vẹn; tái dùng nguồn
công khai đã đóng băng và chỉ bổ sung nến/funding Binance công khai còn thiếu.

Root dữ liệu mới: `~/.local/state/aigorithmic-trading/jev-review/data-20261016/`.
`prepared.json` SHA-256 **`804d9347b38322aec3ff45497a521916b6721f40e3433df99bcc9e85748ed346`**.
Database nghiên cứu SHA-256 **`813514b6d1d1aeff564ac5f14dff039bebcaa7a3556f385d92ec82468b1983ca`**.
Manifest giữ hash database nguồn, nguồn nến cũ và mọi snapshot mới; kiểm lại lineage.
Không đọc VPS để lấy `gates.txt`. Tái dựng local cho đúng **340 lệnh đóng**, khớp
số lệnh từng route trong báo cáo G4.

Receipt local: `state/verification/jev-review-protected-20261016/` (gitignored).

- `boundary-check.json`: 99/99 hash gốc trước sửa.
- `protected-final.json`: manifest cuối, trước/sau đúng ba file; 96 file còn lại nguyên.
- `reference-replay-final.json`: 10 ca H4/slot theo báo cáo advisory; so result_id và summary gốc.
- `disabled-base.json`, `disabled-final.json`: replay 10 route G4 dưới checkout gốc
  và bản cuối; SHA-256 của **toàn bộ JSON output chuẩn hóa**, không chỉ summary.
- `full-pytest-approved-final.txt`, `feature-approved-final.txt`, `review.md`: test và hai trục review.
- `manifest.json`: checksum các receipt, dry-run và nguồn dữ liệu chính.

| Cổng kiểm cuối | Kết quả |
|---|---|
| Full pytest trên mã `6518813` | **1.857 passed, 1 failed, 23 warnings**, 488,38 giây |
| Test mới | **49 passed** |
| 10 tham chiếu H4/slot | **10/10 result_id và summary khớp** bản gốc |
| 10 route G4, lớp phủ tắt | **Toàn bộ output JSON khớp từng byte** giữa base và bản cuối; 340 lệnh |
| Nguồn được bảo vệ | **96/96 giữ nguyên**, đúng ba file được duyệt đổi hash |
| Review độc lập Standards / Spec | **0 / 0 finding còn mở** |
| Control giả trên lịch sử, có kiểm input binding | **10/10 route hoàn tất**, đủ bốn nhãn; 134 veto; 42 counterfactual độc lập bổ sung |
| Credential/inference thật | **Không đọc credential, 0 lần gọi**; cache thật chưa được tạo |

Lỗi full suite duy nhất đúng mục được ghi chú cho phép:
`tests/test_perp_collection_migration.py::test_cli_inheritance_and_readiness_show_collection_without_changing_champion`.
Readiness trả `gate_version='v1'`, test đòi `gate-v2.1`. Không sửa test, skip/xfail
hay nới assertion để che lỗi. Các nhóm test tập trung/reviewer có giao nhau, không cộng tổng.

Control giả có 796 nhãn: AGREE 171, REDUCE 166, VETO 235, NO_VIEW 224. Không phải
mọi nến được vào lệnh vì còn vị thế/gate. 221 dòng VETO/REDUCE được đối chiếu:
179 có lệnh baseline và 42 dùng isolated flat-start; không còn counterfactual thiếu
ở nhóm này. Không dùng kết quả giả để tune ngưỡng hoặc kết luận Jev có ích.
Artifacts ở `~/.local/state/aigorithmic-trading/jev-review/synthetic-controls-20261016-verified/`.

Manifest cuối SHA-256:
**`c1996071c08fe5a4ab476eff18d29785bb1d8e4d09cef81b86361157722d8399`**.
Đã kiểm độc lập mọi checksum receipt và artifact external trong manifest: tất cả khớp.
Giữ receipt manifest thử đầu dưới tên `manifest-initial-invalid.json`: nó hash một
log stdout còn mở; đã đóng log và tạo manifest cuối, không đổi source/test/replay.



Các ID tham chiếu giữ nguyên (summary cũng khớp):

| Ca | result_id |
|---|---|
| `ETH-NEAR-SOL-H4` | `b99e0c1b3885a3b06a8158df0e1b7e8f09167b02453c48ebf7d4153639f04c36` |
| `ETH-NEAR-SOL-H4-cost2x` | `b404fd46a114937ff3dbeabdac54e29d2578ff78336f7ec188b3c422db213508` |
| `ETH-NEAR-SOL-ZEC-ROT-SLOT` | `7ead20459f2ded4f672347cc245b1be07903d050d82220f3546dcf59cd281376` |
| `ETH-NEAR-SOL-ZEC-ROT-SLOT-cost2x` | `02857ed610e9da55d1f6c27afd67e2789face50862c38c529d364292cc812e8c` |
| `ETH-NEAR-SOL-ZEC-ROT-SLOT-W25` | `a175c74f9c2156c7a942b0d5da9150af3d333c525589b56f1d0d23e6bb1f3992` |
| `ETH-NEAR-SOL-ZEC-ROT-SLOT-W25-cost2x` | `68d9022a6dfd366aa6bf376e9d51674ca9bbab8bdfa5157b6b9f98e86cd9446d` |
| `BTC-ETH-NEAR-SOL-ZEC-ROT-SLOT` | `d0c3c35d91efccdd793bd99255cdf7a4000c7c4bef02f041127a6488e97b9cbc` |
| `BTC-ETH-NEAR-SOL-ZEC-ROT-SLOT-cost2x` | `9d350d6286d9959d0922ffb9c1db7e413d89223a2d9bd0b6d336dde9629fd5a4` |
| `BTC-ETH-NEAR-SOL-ZEC-ROT-SLOT-W25` | `8c9ebcfa4f8d556be296e9e9f60a83897d236d99b9b0162b03db4e97b2af38e7` |
| `BTC-ETH-NEAR-SOL-ZEC-ROT-SLOT-W25-cost2x` | `1928fbb0c9e82d4e5f2039e9f7135357ab9cff372d539086d537a736b25ebb27` |

Ba file được duyệt, SHA-256 trước → sau (96 file còn lại nguyên từng byte):

| File | Trước | Sau |
|---|---|---|
| `intraday/replay_v2/contracts.py` | `b04e2de48ba300ad95e0691efd4c3504286309bd1cf2bc7ead0b66cc998ff546` | `4cb314d9c8873e25bf40a06e4d690770504e5412cd7f23cb0bf6e32e1fc617ab` |
| `intraday/replay_v2/engine.py` | `7494b3fecc183cee3983930003cfd358009d2ed3fabd16edfed89e56b4467640` | `f6f4166cc766ca2910aa5b28dfcde617e4d094483af4831340662573c2e8a17e` |
| `intraday/replay_v2/metrics.py` | `810d3f88d5bb92fa678473605993b744304e5ad54b8e3df9f2a0de9db00f6dd6` | `b3a7009e10309ffa3cc5b200f7fbd0514054d91e331bd77be9195cb377eb1037` |

SHA-256 `protected-final.json`:
**`86b0c958536d5ef3d5ba1206f1554bf17bdd5b5e8bb37184fb703055f4051902`**.

## Cách chạy lại

Dùng venv hiện có; không sync/cài dependency, bảo đảm import đúng worktree:

```bash
export UV_PROJECT_ENVIRONMENT=/home/julius/julius-workspace/ai-labs/projects/system-trading/.venv
export PYTHONPATH=/home/julius/julius-workspace/ai-labs/projects/system-trading-jev-review
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen --no-sync pytest -q -p no:cacheprovider
```

Dry-run (đổi `--output` thành đường dẫn mới nếu đã tồn tại):

```bash
uv run --frozen --no-sync python -m intraday jev-review collect --dry-run \
  --database ~/.local/state/aigorithmic-trading/jev-review/data-20261016/research.sqlite3 \
  --funding-dir ~/.local/state/aigorithmic-trading/jev-review/data-20261016 \
  --end 2026-10-14T00:00:00+00:00 \
  --output /tmp/jev-review-dry-run-new.json
```

`aigt jev-review replay` nhận cùng database/end/funding-dir/cache và `--report-dir`,
chỉ đọc cache, ghi hai bộ artifact và bảng counterfactual. `--execute` **chưa được
duyệt và chưa chạy**; khi được duyệt cần `--approved-plan`, plan hash, `--secrets-file` và `--profile-id`
tường minh. Credential chỉ truy cập qua `ProviderSecretStore`, không in key/token.

`scripts/prepare_jev_review_data.py` dựng bản sao/backfill với snapshot checkpoint.
`scripts/verify_jev_review_disabled.py` chạy từng checkout và hash toàn bộ output.
`scripts/verify_jev_review_controls.py` chỉ tạo cache **SYNTHETIC** riêng, gán
nhãn bằng byte đầu của SHA-256 modulo bốn, độc lập với giá/PnL, để kiểm replay
lịch sử có đủ AGREE/REDUCE/VETO/NO_VIEW. Kết quả đó không đo Jev hay giá trị của A.

## File và commit local

- Source mới: `intraday/setup2_review.py`, `jev_review_cache.py`, `jev_review_provider.py`,
  `jev_review_overlay.py`, `jev_review_counterfactual.py`, `jev_review.py`, `jev_review_cli.py`.
- Source sửa: `intraday/__main__.py` (đăng ký/dispatch CLI riêng) và đúng ba file bảo vệ
  `intraday/replay_v2/contracts.py`, `engine.py`, `metrics.py`.
- Công cụ: ba script `prepare_jev_review_data.py`, `verify_jev_review_disabled.py`,
  `verify_jev_review_controls.py` trong `scripts/`.
- Test: `test_setup2_review.py`, `test_setup2_review_cache.py`, `test_jev_review_overlay.py`,
  `test_jev_review_collect.py`, `test_jev_review_provider.py`, `test_jev_review_cli.py`,
  `test_jev_review_controls.py`.
- Tài liệu: báo cáo này, `jev-review-protected-scope-2026-10-16.md` và `CHANGELOG.md`.

Commit mã được kiểm cuối: `6518813` (các slice trước: `c99b431`, `4b9d065`,
`533baa0`, `c910181`, `0674681`, `00ec54c`). Commit báo cáo cuối được ghi sau kiểm chứng.

## Giới hạn và bước chưa làm

- Chưa có câu trả lời Jev thật, chưa chọn ngưỡng theo tuning, chưa bootstrap/calibration
  hoặc so lãi/DD của A. Không kết luận A có ích từ control giả.
- Chốt chia thời gian từ đầu: tuning trước 2026-01-01, kiểm từ 2026-01-01.
  Đợt 2 chỉ tune trên tuning bằng lưới nhỏ, khóa cấu hình rồi kiểm 2026. Không
  kết luận cải thiện nếu CI chênh lệch lãi trung bình giai đoạn kiểm còn chứa 0.
- Jev có thể đã học dữ liệu quá khứ dù state không có coin/ngày; chỉ một chế độ
  thị trường, mẫu 340 lệnh nhỏ và nhiều tín hiệu/nhiều lệnh giả định tương quan.
- **Không cắm lớp phủ vào replay danh mục `donchian_rotation_*` trong đợt này**:
  đây là engine khác, nằm trong 96 file bảo vệ không được sửa. Chỉ chạy lại chúng
  làm đối chứng. Lớp phủ danh mục là task sau cần duyệt phạm vi riêng.
- Chưa làm shadow live, hội đồng LLM/nhánh B hoặc vòng học mỗi tuần.
- Không đọc env/secrets runtime, không gọi inference/Telegram/Binance order/Demo,
  không đổi rule hash, active set, soak, scoped gate hoặc hành vi Demo; không push,
  merge/deploy, xóa file hay ghi `.hermes/MEMORY.md`.
