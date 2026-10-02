# Weekly gate v2 — Spot và Perp

Đã xây và kiểm thử local ngày 2026-10-02; chưa push/deploy/bật policy trên VPS.
Source schema v23 giữ nguyên. Đây là automation **đánh giá**, không phải automation
giao dịch hoặc tối ưu rule. [Quyết định kiến trúc](decisions/001-weekly-gate-automation.md).

## Trình tự và lịch

- Historical replay v2 → operator start validation → đánh giá validation v2 →
  operator review/promote → execution acceptance/activation riêng.
- Lần đầu khi đủ minimum: Spot ≥365 ngày nến 4h hợp lệ; Perp ≥14 ngày collection.
  Validation Spot/Perp vẫn cần ≥14 ngày **từ campaign start**.
- Sau evaluation chưa pass, chờ ≥7 ngày kể từ evaluation đó trong cùng
  candidate/phase/campaign. Evaluation v2 chạy tay cũng được tính vào lịch.
- Window tích lũy từ đầu collection/campaign, không cắt từng đoạn 7 ngày hoặc
  reset clock. Minimum samples/coverage/cost/risk giữ nguyên; đủ thời gian chưa
  đồng nghĩa pass. History thiếu chờ refresh từ collector/baseline job.
- Pass dừng đánh giá **giai đoạn đó**, tạo operator alert dedup theo evaluation ID,
  `/assets` hiện “Gate pass · chờ operator”. Operator start validation bằng đúng
  pass ID thì scheduler theo campaign mới. Không auto-start/promote/trade.
- Replay economic reject/deferred retry sau 7 ngày. Hard-risk hoặc lỗi binding/prefix
  dừng để điều tra. Validation reject kết thúc campaign, không tự tạo campaign mới.
- Policy theo market áp dụng mọi enabled catalog coin, cả coin thêm sau. Coin chưa
  có data adapter/history/rule hợp lệ hiện blocker. V1-rejected Spot có thể được
  audit bằng v2; v1 rejection và payload vẫn giữ nguyên.

## Lệnh quản trị

Chạy **sau khi** deployment và backup được operator phê duyệt. Các lệnh không tự
build/redeploy image. Với native/copy, thêm `--database /ABS/PATH/source.sqlite3`.

```bash
aigt assets rules migrate-perp BTC ETH HYPE NEAR ZEC SOL --dry-run
aigt assets rules migrate-perp BTC ETH HYPE NEAR ZEC SOL
aigt assets rules automation set --market all --interval-days 7
aigt assets rules automation status --market all
aigt assets rules automation pause --market all
```

`--market spot` / `--market perp` cấu hình độc lập. Dry-run và automation status
chỉ đọc, không DDL/initialize DB thiếu/cũ. Batch migration báo lỗi theo coin và
exit nonzero khi partial failure; sửa nguyên nhân rồi retry. Không chọn candidate
không xác định hoặc giả provenance. Weekly replay không cần API key/model.

BTC Perp: clone candidate cùng parameters từ champion, giữ champion reference;
inherit collection ghi thật, không giả `created_at` cũ. Perp baseline khác dùng
candidate và registry anchor hiện có, không restart. Rule hash/prefix checksum
immutable; retry trả binding đã lưu. Policy không tự migrate champion.
Perp mới được chuẩn bị baseline/collection ở job riêng, seal prefix khi có settled
evidence; Spot có bootstrap/history riêng. Không tự chọn rule mới để ép pass.

Sau selection/policy, lệnh rule mặc định dùng v2; explicit `--engine v1`, generic
legacy lifecycle và BTC/parent simulator activation không được bypass. Demo scoped
admission yêu cầu exact promoted v2 evaluation. Không tự đổi active execution.

## Worker, funding và artifacts

`portfolio soak`/paper worker có gate loop riêng poll 60 giây; collector không chờ
engine. OS file lock serialize theo database, chia sẻ với manual gate/migration CLI.
Ledger giữ candidate/phase/campaign/cutoff, funding ID, attempts và evaluation ID.
Restart sau record reconcile gate đã lưu, không chạy engine trùng. Chưa có evaluation
thì resume intent tại cutoff cũ. Orphan/unpublished reports giữ nguyên, không tự xóa.

Perp thu public Binance funding đúng symbol/window trước engine, lưu snapshot immutable.
Funding lỗi retry tối đa 3 attempts, cách 15 phút; hết attempts ghi error và chờ cycle
mới sau 7 ngày. Không giả funding 0. Binding/report/funding sai dừng job, không sửa
bằng chứng. Weekly gate không gọi Jev/LLM; collector và confidence research advisory
là workflow riêng. Auto-tune legacy bị chặn trên route đã chọn v2.

Reports dùng replay report root hiện có / `INTRADAY_REPLAY_REPORT_DIR`; Docker dùng
volume `replay-reports`, admin/worker ghi và web chỉ đọc. Policy/jobs/selection nằm
trong optional tables source DB, không đổi v1 evaluation/payload. Readiness GET chỉ
chiếu dữ liệu, không engine/network/DDL. Trading/scheduling UTC; dashboard UTC+7.

## Rollout và rollback

1. Backup source/reports; ghi worker ID/start, rule hashes, collection anchors,
   BTC/ETH Spot campaign IDs/start và execution state.
2. Deploy **worker/admin/web tương thích v2 trước** migrate/set policy. Không để
   worker v1-only viết gate song song sau selection. Restart worker không reset DB
   collection/campaign; deployment vẫn cần approval riêng.
3. Dry-run → migrate → set policy → kiểm tra status/dashboard/job/report binding.
   Chưa bật Demo hoặc gửi order; minimum và risk policy không đổi.
4. Rollback: `automation pause --market all`, giữ code tương thích và mọi binding.
   Pause không khôi phục v1 admission. Không drop tables, restore DB cũ hoặc revert
   sang v1-only writer trong khi collector đang chạy. Operator điều tra và quyết định
   candidate/campaign mới hoặc resume; không sửa clock/hash để ép pass.

## Kiểm chứng local

- Regression: **918 tests pass**, 18 dependency deprecation warnings; build pass.
- Engine Spot/Perp thật, manual scheduling, cumulative windows, funding retry,
  crash recovery, hard-risk/prefix halt, lock, future baseline và admission guards.
- Snapshot soak 529 MB copy: dry-run/migration/retry BTC/ETH/HYPE/NEAR/ZEC/SOL đều
  thành công; rule payload cũ, registry, lifecycle, campaign và v1 evaluation giữ nguyên.
  Đây là kiểm chứng trên copy, không phải trạng thái VPS mới.
- Chrome isolated: Spot/Perp tabs, pass/waiting state, UTC+7, keyboard details,
  accessibility table và widths 320/768/1024/1440; API 200, console/network sạch.

Temporary artifacts: `/tmp/aigt-weekly-acceptance-Kduqdz/` và
`/tmp/aigt-weekly-browser-3wm2yid4/`; không thay thế backup.
