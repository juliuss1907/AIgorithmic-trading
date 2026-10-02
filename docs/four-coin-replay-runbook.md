# Four-coin replay và Perp confidence review

Implementation local 2026-10-02. Study ETH, NEAR, ZEC, SOL; HYPE không thuộc batch
này nhưng dữ liệu/collector vẫn giữ nguyên. BTC, champion, gate v1/v2, campaign và
execution không bị study thay đổi. Xem [kết quả kiểm chứng](four-coin-replay-verification.md).

## CLI

```bash
aigt replay study --coins ETH NEAR ZEC SOL
aigt replay confidence review ETH
aigt replay confidence status ETH
```

`study` chạy Spot và Perp; `confidence review` chỉ Perp. Các lệnh nghiên cứu tạo
SQLite backup nhất quán có integrity/checksum, rồi làm việc trên một bản sao riêng.
Nến 4h và model-call journal chỉ được bổ sung vào research copy. Không nhập Binance
key; review dùng LLM profile active trong source snapshot và provider-secret file
khớp profile đó. Không tự chọn provider khác nếu thiếu key.

Khi image Docker chưa có code này, chạy native với đường dẫn rõ ràng:

```bash
uv run aigt replay study --coins ETH NEAR ZEC SOL \
  --database /ABS/source.sqlite3 --report-dir /ABS/replay-reports
uv run aigt replay confidence status ETH --report-dir /ABS/replay-reports
```

Chọn `--database` hoặc `--report-dir` tắt Docker routing. Không chọn đường dẫn
native thì CLI dùng deployment registry hiện có và admin image. Source cần schema
v23; lệnh nghiên cứu không migrate source cũ. `status` chỉ đọc review, không cần
source hoặc key, không tạo report root nếu chưa có.

`--secrets-file FILE` có ưu tiên hơn `INTRADAY_PROVIDER_SECRETS_FILE`, rồi mới tới
provider-secret path mặc định. `--offline` chỉ tắt tải nến/funding công khai; **không
tắt lời gọi LLM** nếu đủ evidence. Hiện review offline không tự nạp funding artifact
cũ nên sẽ deferred khi không có funding coverage; dùng `replay run --funding-id`
cho replay offline riêng. Không đặt key trong đối số hoặc commit credential file.

## Spot

- 24 tháng lịch closed native Binance 4h: 18 tháng selection, 6 tháng holdout.
  Có warm-up trước selection; thiếu history/coverage → deferred, không rút ngắn.
- ETH: rule hiện có và 30/8. NEAR/ZEC/SOL: hiện có, 30/8, 40/8, 50/8. Parameter
  giống nhau chỉ chạy một lần; variant dùng ATR14, các bộ lọc khác giữ nguyên.
- Selection cần ≥6 closed trades, net >0, DD <8%, không vi phạm hard risk.
  Chọn net return trừ DD, tie chọn DD thấp hơn. Chỉ rule đã chọn chạy holdout;
  holdout fail thì không chọn lại rule khác dựa trên holdout.
- Mỗi run tài khoản độc lập 1000 USDT, fee10 + slippage5 bps/fill, stop10%, daily
  guard1.5%, DD guard8%; next-open execution và deterministic risk của engine cũ.
  Guard có thể halt tới cuối run; không reset theo tháng để tăng số trades.
- Replay không dựng Jev lịch sử. Pass chỉ là nghiên cứu Donchian/ATR với giả định
  ledger; không chứng minh Jev/LLM hoặc Demo execution đã được kiểm chứng.

Historical holdout không phải prospective OOS: rule hiện có có thể đã được tạo sau
đầu cửa sổ nghiên cứu. Report giữ limitation ex-post; vẫn cần validation mới.

## Perp và confidence

Tối đa90 ngày Jev numeric-v1 primary, schema2, snapshot/model-call provenance đã
lưu, quotes/mark và funding thật; selection70% / holdout30% theo thời gian.
LLM chỉ nhận summary selection và observed confidence frontier. Frontier lấy từ
probability quan sát được để cung cấp evidence, không giới hạn proposal vào các
mốc tròn. Holdout không được gửi trong model payload.

Confidence hữu hạn **69%–100% inclusive**: sàn mục tiêu70%, dung sai1 **điểm phần
trăm**. Chấp nhận số lẻ và >85%; giữ nguyên số69%, không clamp thành70%. Từ chối
<69%, >100%, NaN/Infinity. Filter giao dịch so sánh với đúng threshold đã được duyệt,
không trừ thêm1% lần nữa. Confidence cao hơn không tự chứng minh return tốt hơn.

Chỉ threshold thay đổi theo coin. Stop, filters, risk, leverage3x và rule cũ giữ
nguyên; fee5 + slippage5 bps/fill, spread trong bid/ask, funding riêng. Proposal
cần replay training/holdout đủ trades và không vi phạm risk; lịch sử <14 ngày chưa
đủ điều kiện formal gate. LLM có thể trả `insufficient_data`, không có fallback rate.

## Weekly review: mặc định tắt

Trong cấu hình worker, chỉ bật sau khi operator quyết định:

```dotenv
INTRADAY_CONFIDENCE_REVIEW_ENABLED=true
INTRADAY_CONFIDENCE_REVIEW_SYMBOLS=ETHUSDT,NEARUSDT,ZECUSDT,SOLUSDT
```

Thứ Hai, tick đầu tiên từ09:00 Asia/Ho_Chi_Minh; bootstrap loop kiểm tra mỗi giờ,
không phải timer đảm bảo chạy đúng giây09:00. Ngoài thứ Hai không catch-up. Cần
≥100 verified training decisions, coverage≥95% và ≥100 decisions mới trong training
so với model-review watermark. Các symbol scheduler chỉ cho phép bốn coin này.
Khi bật, generic auto-proposal Perp của các symbol đó bị bỏ qua để tránh cạnh tranh;
Spot và các symbol khác giữ workflow hiện có.

Intent được lưu atomically **trước** simulation/model call, tối đa một lần review
được claim/coin/tuần; không tự retry dù lỗi hoặc restart. Preflight deferred cũng
chiếm weekly slot nhưng không tiến watermark model. `running` tồn tại sau crash
cần operator kiểm tra; không tự gọi model lại trong tuần. Pending proposal chặn
proposal cạnh tranh tuần sau. Dùng cùng report root, không đổi root để né budget.

Nếu operator quyết định không dùng pending proposal:

```bash
aigt replay confidence dismiss ETH --review-id REVIEW_ID
```

Chỉ dismiss đúng pending review; không sửa source/rule/gate, không apply threshold.
Tuần đã claim vẫn không được retry. `REVIEW_ID` lấy từ status, không phải rule ID.

## Report và dashboard

Root: `--report-dir` → `INTRADAY_REPLAY_REPORT_DIR` → XDG state mặc định
`~/.local/state/aigorithmic-trading/reports/replay-v2`.

- `studies/STUDY_ID/manifest.json`: kết quả từng route, baseline integrity/hash,
  `research_evidence_database`/checksum/integrity trỏ tới final backup trong
  `evidence/`. Baseline và evidence backup không được dùng để ghi; working copy
  có history bổ sung và có thể thay đổi byte khi mở lại để chẩn đoán/checkpoint.
- `confidence/reviews.sqlite3`: weekly intents/proposals có payload checksum, tách
  khỏi source registry và execution journal.
- `RUN_ID/`: account replay bundles dùng publisher private/checksum hiện có.

Docker Compose mới mount `replay-reports` read-write cho admin/worker, read-only
cho web, cùng `/app/state/replay-reports`. Flag vẫn default off; sửa Compose không
phải redeploy. Cần giữ volume này khi recreate worker để giữ budget và proposal.

Web `/assets` → tab Perp → **Confidence proposals**. Chỉ GET, hiển thị source rule
(có thể queued, không có nghĩa đang trade) → proposal, model/rationale, blockers,
holdout metrics và report links. Lịch sử UTC+7; UTC vẫn là clock dữ liệu/trading.
API `/api/replay/confidence?symbol=ETH&offset=0&limit=20`, limit tối đa100.
Không có Telegram, nút auto-apply hoặc quyền trading từ màn hình này.

## Bước sau nghiên cứu

Operator tạo candidate canonical bằng lifecycle hiện có, đúng champion/bootstrap
parent và các parameter đã được xem xét; không import tùy tiện research variant.
Sau đó formal gate v2 → explicit fresh validation ≥14 ngày → evaluate/promote.
Proposal `pending_review` hoặc study `pass` **không** tạo gate evaluation ID,
không start campaign, promote hoặc kích hoạt Binance Demo. Xem
[operator gate runbook](replay-v2-runbook.md#operator-gate-v2-replay--validation-mới--promote).
