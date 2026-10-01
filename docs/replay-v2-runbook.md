# Replay v2 — offline research, không phải activation gate

Implementation local 2026-10-01, evaluator `replay-v2.1`; source schema v23 giữ nguyên.
V2 mô phỏng một coin/một market mỗi run. Không gửi lệnh, gọi model, refresh history,
đọc API key hoặc ghi vào source DB. Không thay `aigt assets rules replay`, legacy
Perp replay, champion/challenger, soak hoặc quyền execution.

## Chạy và xem kết quả

Các timestamp phải có timezone; `--from` inclusive, `--to` exclusive. Code chuyển
sang UTC; web hiển thị UTC+7. Chọn rule ID đúng symbol/scope, không nhất thiết đã pass.
Spot v2 chỉ nhận scope `spot_4h`, không dùng champion `spot_daily` legacy.

```bash
aigt replay run ETH --market spot --rule RULE_ID \
  --from 2026-09-01T00:00:00Z --to 2026-10-01T00:00:00Z
aigt replay run ETH --market perp --rule RULE_ID \
  --from 2026-09-28T00:00:00Z --to 2026-10-01T00:00:00Z --leverage 3
aigt replay list
aigt replay show RUN_ID
```

`RULE_ID` và `RUN_ID` là placeholders, không copy như ID thật. Coin mới dùng cùng
lệnh nếu đã đăng ký scope và có rule/evidence; không có nhánh riêng BTC/ETH.
Không yêu cầu 365 ngày hoặc 14 ngày cho report nghiên cứu này; minimum/gates v1
vẫn giữ nguyên. Khoảng thời gian ngắn không chứng minh hiệu quả chiến lược.

Nếu có deployment registry và không chọn đường dẫn native, CLI route tới Docker
admin. Image admin/web phải chứa feature này; không tự build/redeploy từ lệnh replay.
`--config` trong Docker được bind đúng một file JSON chỉ-đọc; không mount thêm key.

Chạy native rõ ràng khi test code local, không route sang image Docker cũ:

```bash
uv run aigt replay run ETH --market spot --rule RULE_ID \
  --from 2026-09-01T00:00:00Z --to 2026-10-01T00:00:00Z \
  --database /ABS/PATH/source.sqlite3 --report-dir /ABS/PATH/replay-reports
uv run aigt replay list --report-dir /ABS/PATH/replay-reports
uv run aigt replay show RUN_ID --report-dir /ABS/PATH/replay-reports
```

Database thiếu/cũ hoặc rule sai scope báo lỗi; không tạo DB hay migrate. Chọn
`--database` hoặc `--report-dir` là opt-out Docker routing. `list/show` không cần
đọc database. Không dùng `assets rules status` như read-only command nếu đang kiểm
chứng source tuyệt đối không ghi; tìm ID từ report/readiness hoặc SQL read-only.

## Report lưu ở đâu?

Native mặc định:
`$XDG_STATE_HOME/aigorithmic-trading/reports/replay-v2/<run_id>/`, hoặc
`~/.local/state/aigorithmic-trading/reports/replay-v2/<run_id>/` khi XDG chưa cấu hình.
Ưu tiên `--report-dir` → `INTRADAY_REPLAY_REPORT_DIR` → mặc định.

Docker: named volume `replay-reports`, mount `/app/state/replay-reports`:
admin ghi, web chỉ đọc, worker không mount. Thay Compose file không phải deploy;
không start/recreate worker hay Demo execution trong triển khai feature local.

Bundle gồm `manifest.json`, `summary.json`, `summary.md`, `equity_curve.jsonl`,
`trades.jsonl`, `events.jsonl`; folder 0700, files 0600. Manifest công bố atomically
sau khi ghi/fsync artifacts. Run lỗi để lại directory chưa publish; catalog bỏ qua,
không tự xóa. Run ID mới mỗi lần, không ghi đè; result ID giống nhau khi input/config/
evaluator giống nhau. Checksum kiểm chứng artifact, không thay cho backup source.

Web `/replay` và `/replay/RUN_ID` chỉ đọc. Khi chạy native với root tùy chọn, đặt
`INTRADAY_REPLAY_REPORT_DIR` cho web trùng root CLI, hoặc truyền `replay_report_dir`
khi gọi `create_app`. Không nhập API key vào replay/dashboard.

API chỉ GET:

- `/api/replay/runs?offset=0&limit=20` (limit tối đa 100).
- `/api/replay/runs/RUN_ID`: summary, assumptions, checksums, preview equity ≤200 điểm.
- `/api/replay/runs/RUN_ID/equity_curve`, `/trades`, `/events`: offset/limit,
  tối đa 1000 rows/request. Trang HTML dùng 20 trades/30 events mỗi trang.

Không trả raw prompts, state snapshots, credential files hay local artifact paths.
IDs/path/series được kiểm tra; symlink và checksum sai bị từ chối.

## Mô hình và giả định

Defaults: 1000 USDT (≤10000), weight 1; Spot cap 30%, Perp cap 20% **vốn ban đầu**;
target còn bị giới hạn theo equity hiện tại. Phần chưa dùng là cash. Perp isolated
3x, override 1–10x; leverage thấp có thể giảm target để đủ margin room, không nhân
notional/PnL. Margin entry limit 10% dựa trên pre-fill equity; tỷ lệ mark-to-market
sau phí/biến động có thể khác, được báo thật trong report.

- Spot: Donchian trên closed native 4h; cần đủ contiguous warm-up bars trước entry,
  ATR size multiplier một lần; next-open fill, Donchian exit độc lập với stop 10%.
  Không dựng Jev cho lịch sử. Nếu window cắt giữa nến, chỉ mô phỏng nến 4h trọn vẹn.
  OHLC giả định open → adverse → favourable → close; intrabar timestamp ước tính.
- Perp: numeric-v1 primary Jev đã lưu, feature schema 2. Xác minh snapshot và
  archived successful model-call fingerprint, không dùng assignment hiện tại.
  Khớp quote hợp lệ đầu tiên **sau** decision availability (kể cả model completion),
  decision age ≤45s, entry spread ≤10bps/dislocation ≤1%. Entry dùng ask/bid,
  stop trigger dùng mark; gap fill ở quote khả dụng, không khớp giả ở stop.
- Một position mỗi route, không pyramid/flip/re-entry tại quote đóng. TakeProfit
  đóng position theo Demo semantics, không phải outcome directional-return 15 phút.
- Daily loss 1.5%, DD 8%, hardcoded và không model-tunable; terminal halt tới hết
  run. Phí đóng cũng có thể kích hoạt guard. Gap có thể làm lỗ vượt threshold;
  stop/risk guard không phải đảm bảo mức lỗ tuyệt đối.
- Cuối run đóng ở giá cuối hợp lệ với `window_end`. Nếu thiếu price tail, report
  ghi rõ: không kéo forward giá, không giả định giữ position qua funding tương lai
  rồi backdate lệnh đóng. Exit cuối cửa sổ là giả định nghiên cứu, không tín hiệu.

Chi phí gộp **giả định**, Spot 15bps và Perp 5bps mỗi fill, debit vào cash đúng một
lần. Spread quote Perp là chi phí bổ sung qua bid/ask. Không tách fee/slippage giả
hoặc khẳng định đây là biểu phí Binance Demo thực tế. Giá nguồn là market data
Binance đã lưu, không được xác minh là historical Demo quotes.

`--config FILE.json` chỉ nhận capital/leverage/profile. CLI `--capital`/`--leverage`
override file, mặc định dùng `bnb-demo` profile v1. Ví dụ chỉ thay giả định chi phí:

```json
{
  "capital": "1000",
  "leverage": 3,
  "profile": {"profile_id": "bnb-demo", "version": "1", "spot_cost_bps": "15", "perp_cost_bps": "5"}
}
```

Profile có thể thêm `instrument` theo contract `InstrumentRules`, bắt buộc
`instrument_source` + `instrument_observed_at`; symbol phải đúng coin. Đây là
metadata snapshot, không phải historical filters/maintenance tiers. Không có
filters thì quantity lý tưởng hóa và `instrument_filters_not_verified`; không
khẳng định Binance Demo hỗ trợ coin/order type chỉ vì replay có PnL.

Funding optional: `profile.funding` gồm `symbol`, `source`, `coverage_start`,
`coverage_end`, `settlements` (mỗi row: aware `at`, Decimal `rate`, positive `mark`).
Timestamp unique/ordered trong coverage. Chỉ khai báo coverage thật đã kiểm chứng;
đừng khai báo empty/full coverage để biến dữ liệu thiếu thành funding 0.
Long trả rate dương, Short nhận; rate âm đảo dấu. Không dùng lastFundingRate/OI
hoặc tự dựng lịch funding làm settlement. Coverage thiếu ⇒ `net_pnl/net_return_pct`
null; chỉ hiện PnL/risk sau chi phí đã biết, đường đi risk có thể đổi khi bổ sung
funding đầy đủ. ES là tail mean 5% của **UTC daily equity returns**, ghi số mẫu.

V1 evaluation trong report chỉ tham chiếu; Spot v1 dùng folds khác, Perp v1 scoring
là decision-quality, không phải account return. Rule chọn sau start window được
gắn nhãn ex-post, không được gọi là OOS. Không auto tune để ép pass hoặc activate.

Chưa mô phỏng full order book, partial fills, API/reconciliation failures,
liquidation chính xác hoặc portfolio đa coin. Hyperliquid cần profile, dataset
mapping và acceptance riêng; engine không chứa Binance API calls nhưng hiện chỉ
`bnb-demo` được hỗ trợ. Không đổi profile name để giả hỗ trợ sàn khác.
